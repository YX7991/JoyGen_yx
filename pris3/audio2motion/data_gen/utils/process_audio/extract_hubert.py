# Copyright (c) 2025 JD.com, Inc. and affiliates.
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     https://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# This file contains code from Real3DPortrait (Copyright (c) 2024 ZhenhuiYe),
# licensed under the MIT License, available at https://github.com/yerfor/Real3DPortrait.

from transformers import Wav2Vec2Processor, HubertModel
import soundfile as sf
import numpy as np
import torch
import os
from pris3.audio2motion.utils.commons.hparams import set_hparams, hparams


wav2vec2_processor = None
hubert_model = None


def get_hubert_from_16k_wav(wav_16k_name, model_path):
    speech_16k, _ = sf.read(wav_16k_name)
    hubert = get_hubert_from_16k_speech(speech_16k, model_path)
    return hubert

@torch.no_grad()
def get_hubert_from_16k_speech(speech, model_path, device="cuda:0"):
    global hubert_model, wav2vec2_processor
    #model_path = './hubert'
    if hubert_model is None:
        print(f"Loading the HuBERT Model from {model_path}")
        if os.path.exists(model_path):
            hubert_model = HubertModel.from_pretrained(model_path)
        else:
            hubert_model = HubertModel.from_pretrained("facebook/hubert-large-ls960-ft")
    hubert_model = hubert_model.to(device)
    if wav2vec2_processor is None:
        print("Loading the Wav2Vec2 Processor...")
        if os.path.exists(model_path):
            wav2vec2_processor = Wav2Vec2Processor.from_pretrained(model_path)
        else:
            wav2vec2_processor = Wav2Vec2Processor.from_pretrained("facebook/hubert-large-ls960-ft")

    if speech.ndim ==2:
        speech = speech[:, 0] # [T, 2] ==> [T,]
    
    input_values_all = wav2vec2_processor(speech, return_tensors="pt", sampling_rate=16000).input_values # [1, T]
    input_values_all = input_values_all.to(device)
    # For long audio sequence, due to the memory limitation, we cannot process them in one run
    # HuBERT process the wav with a CNN of stride [5,2,2,2,2,2], making a stride of 320
    # Besides, the kernel is [10,3,3,3,3,2,2], making 400 a fundamental unit to get 1 time step.
    # So the CNN is euqal to a big Conv1D with kernel k=400 and stride s=320
    # We have the equation to calculate out time step: T = floor((t-k)/s)
    # To prevent overlap, we set each clip length of (K+S*(N-1)), where N is the expected length T of this clip
    # The start point of next clip should roll back with a length of (kernel-stride) so it is stride * N
    kernel = 400
    stride = 320
    clip_length = stride * 1000
    num_iter = input_values_all.shape[1] // clip_length
    expected_T = (input_values_all.shape[1] - (kernel-stride)) // stride
    res_lst = []
    for i in range(num_iter):
        if i == 0:
            start_idx = 0
            end_idx = clip_length - stride + kernel
        else:
            start_idx = clip_length * i
            end_idx = start_idx + (clip_length - stride + kernel)
        input_values = input_values_all[:, start_idx: end_idx]
        hidden_states = hubert_model.forward(input_values).last_hidden_state # [B=1, T=pts//320, hid=1024]
        res_lst.append(hidden_states[0])
    if num_iter > 0:
        input_values = input_values_all[:, clip_length * num_iter:]
    else:
        input_values = input_values_all

    if input_values.shape[1] >= kernel: # if the last batch is shorter than kernel_size, skip it            
        hidden_states = hubert_model(input_values).last_hidden_state # [B=1, T=pts//320, hid=1024]
        res_lst.append(hidden_states[0])
    ret = torch.cat(res_lst, dim=0).cpu() # [T, 1024]

    assert abs(ret.shape[0] - expected_T) <= 1
    if ret.shape[0] < expected_T: # if skipping the last short 
        ret = torch.cat([ret, ret[:, -1:, :].repeat([1,expected_T-ret.shape[0],1])], dim=1)
    else:
        ret = ret[:expected_T]

    return ret


if __name__ == '__main__':
    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument('--video_id', type=str, default='May', help='')
    args = parser.parse_args()
    ### Process Single Long Audio for NeRF dataset
    person_id = args.video_id
    wav_16k_name = f"data/processed/videos/{person_id}/aud.wav"
    hubert_npy_name = f"data/processed/videos/{person_id}/aud_hubert.npy"
    speech_16k, _ = sf.read(wav_16k_name)
    hubert_hidden = get_hubert_from_16k_speech(speech_16k)
    np.save(hubert_npy_name, hubert_hidden.detach().numpy())
    print(f"Saved at {hubert_npy_name}")
