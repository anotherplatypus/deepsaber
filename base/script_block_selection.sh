#!/bin/bash

# export PYTHONPATH="/home_directory/.local/lib/python3.5/site-packages/"

py=python3
dataset=stage_two
model=transformer
exp=transformer_testing

$py train.py --data_dir=../AugDataTest --dataset_name=$dataset --model=$model --batch_size=2 --num_windows=0 --nepoch=500 --nepoch_decay=500 \
    --print_freq=10 --experiment_name=$exp --save_by_iter --save_latest_freq=1000 \
    --pad_batches \
    --feature_name=mel \
    --feature_size=100 \
    --d_src=102 \
    --tgt_vocab_size=2003 \
    --label_smoothing \
    --max_token_seq_len=50 \
    --reduced_state \
    --gpu_ids=0 \
    --level_diff=Expert \
    --workers=0 \
    --using_sync_features \
    #--continue_train \
    #--load_iter=7000 \