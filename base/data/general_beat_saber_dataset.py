from pathlib import Path
from itertools import tee
import numpy as np
import torch
import librosa
from base.data.base_dataset import BaseDataset
import json
from math import floor, ceil
import pickle
unique_states = pickle.load(open("../stateSpace/sorted_states.pkl","rb"))
feature_name = "chroma"
feature_size = 24
number_reduced_states = 2000
from .level_processing_functions import get_reduced_tensors_from_level, get_full_tensors_from_level

class GeneralBeatSaberDataset(BaseDataset):

    def __init__(self, opt,receptive_field=None):
        super().__init__()
        self.opt = opt
        self.receptive_field = receptive_field
        data_path = Path(opt.data_dir)
        if not data_path.is_dir():
            raise ValueError('Invalid directory:'+opt.data_dir)
        # self.audio_files = sorted(data_path.glob('**/*.ogg'), key=lambda path: path.parent.__str__())
        candidate_audio_files = sorted(data_path.glob('**/*.ogg'), key=lambda path: path.parent.__str__())
        self.level_jsons = []
        self.audio_files = []
        self.features = {}

        for i, path in enumerate(candidate_audio_files):
            #print(path)
            features_file = path.__str__()+"_"+feature_name+"_"+str(feature_size)+".npy"
            level_file_found = False
            for diff in self.opt.level_diff.split(","):
                if Path(path.parent.__str__()+"/"+diff+".json").is_file():
                    level_file_found = True
            if not level_file_found:
                continue
            try:
                features = np.load(features_file)

                # we need to find out what the input length of the model is, to remove songs which are too short to get input windows from them for this model
                receptive_field = self.receptive_field
                output_length = self.opt.output_length
                input_length = receptive_field + output_length -1

                if features.shape[1]-(input_length+self.opt.time_shifts-1) < 1:
                    print("Smol song; ignoring..")
                    continue

                self.features[path.__str__()] = features
            except FileNotFoundError:
                raise Exception("An unprocessed song found; need to run preprocessing script process_songs.py before starting to train with them")

            #for diff in ["Hard","hard","Expert"]:
            for diff in self.opt.level_diff.split(","):
                #level = list(path.parent.glob('./'+self.opt.level_diff+'.json'))[0]
                try:
                    level = list(path.parent.glob('./'+diff+'.json'))[0]
                    self.level_jsons.append(level)
                    self.audio_files.append(path)
                except:
                    continue


        assert self.audio_files, "List of audio files cannot be empty"
        assert self.level_jsons, "List of level files cannot be empty"
        assert len(self.audio_files) == len(self.level_jsons)
        self.eps = 0.1

    @staticmethod
    def modify_commandline_options(parser, is_train):
        parser.add_argument('--sampling_rate', default=16000, type=float)
        parser.add_argument('--level_diff', default='Expert', help='Difficulty level for beatsaber level')
        parser.add_argument('--hop_length', default=256, type=int)  # Set the hop length; at 22050 Hz, 512 samples ~= 23ms
        parser.add_argument('--compute_feats', action='store_true', help="Whether to extract musical features from the song")
        parser.add_argument('--padded_length', type=int, default=3000000)
        parser.add_argument('--chunk_length', type=int, default=9000)
        # the input features at each time step consiste of the features at the time steps from now to time_shifts in the future
        parser.add_argument('--time_shifts', type=int, default=1, help='number of shifted sequences to include as input')
        parser.add_argument('--reduced_state', action='store_true', help='if true, use reduced state representation')
        parser.add_argument('--using_sync_features', action='store_true', help='if true, use synced features')
        parser.add_argument('--concat_outputs', action='store_true', help='if true, concatenate the outputs to the input sequence')
        ## IF REDUCED STATE
        # the total number of input_channels is constructed by the the nfcc features (20 of them), 16 times one for each time_shift as explained above
        # plus the 2001 classes in the reduced state representation corresponding to the block at that time step
        # parser.set_defaults(input_channels=(feature_size*16+2001))
        parser.set_defaults(input_channels=(feature_size*1+number_reduced_states+1))
        # the number of output classes is one per state in the set of reduced states
        parser.set_defaults(num_classes=number_reduced_states+1)
        # channels is just one, just prediting one output, one of the 2001 classes
        parser.set_defaults(output_channels=1)
        ### IF FULL STATE
        # parser.set_defaults(input_channels=(20*16+12*20))
        # # there are 12 outputs, one per grid point, with 20 possible classes each.
        # parser.set_defaults(output_channels=12)
        # parser.set_defaults(num_classes=20)

        return parser

    def name(self):
        return "GeneralBeatSaberDataset"

    def __getitem__(self, item):
        #NOTE: there is a lot of code repeat between this and the non-reduced version, perhaps we could fix that
        song_file_path = self.audio_files[item].__str__()
        features = song_file_path+"_"+feature_name+"_"+str(feature_size)+".npy"

        # get features
        try:
            features = self.features[song_file_path]
        except:
            raise Exception("features not found for song "+song_file_path)

        level = json.load(open(self.level_jsons[item].__str__(), 'r'))

        bpm = level['_beatsPerMinute']
        features_rate = bpm*self.opt.beat_subdivision
        notes = level['_notes']

        #useful quantities, to sync notes to song features
        sr = self.opt.sampling_rate
        beat_duration = int(60*sr/bpm) #beat duration in samples
        # duration of one time step in samples:
        hop = int(beat_duration * 1/self.opt.beat_subdivision)
        if not self.opt.using_sync_features:
            hop -= hop % 32
        num_samples_per_feature = hop
        #num_samples_per_feature = beat_duration//self.opt.beat_subdivision #this is the number of samples between successive frames (as used in the data processing file), so I think that means each frame occurs every mel_hop + 1. I think being off by one sound sample isn't a big worry though.

        # for short
        y = features
        print(y)

        receptive_field = self.receptive_field
        # we pad the song features with zeros to imitate during training what happens during generation
        y = np.concatenate((np.zeros((y.shape[0],receptive_field)),y),1)

        ## WINDOWS ##
        # sample indices at which we will get opt.num_windows windows of the song to feed as inputs
        # TODO: make this deterministic, and determined by `item`, so that one epoch really corresponds to going through all the data..
        sequence_length = y.shape[1]
        if self.opt.num_windows >= 1:
            input_length = receptive_field + self.opt.output_length -1
            indices = np.random.choice(range(sequence_length-(input_length+self.opt.time_shifts-1)),size=self.opt.num_windows,replace=True)
        elif self.opt.time_shifts == 1:
            indices=np.array([0])
            input_length = sequence_length
        else:
            raise Exception("Can't have time_shifts with setting num_windows=0 (which means take the whole sequence)")

        ## CONSTRUCT TENSOR OF INPUT SOUND FEATURES (MFCC) ##
        # loop that gets the input features for each of the windows, shifted by `ii`, and saves them in `input_windowss`
        input_windowss = []
        for ii in range(self.opt.time_shifts):
            input_windows = [y[:,i+ii:i+ii+input_length] for i in indices]
            input_windows = torch.tensor(input_windows)
            input_windows = (input_windows - input_windows.mean())/torch.abs(input_windows).max()
            input_windowss.append(input_windows.float())

        ## BLOCKS TENSORS ##
        if self.opt.reduced_state:
            blocks_windows, blocks_targets = get_reduced_tensors_from_level(notes,indices,sequence_length,self.opt.num_classes,bpm,sr,num_samples_per_feature,receptive_field,input_length)
        else:
            blocks_windows, blocks_targets = get_full_tensors_from_level(notes,indices,sequence_length,self.opt.num_classes,self.opt.output_channels,bpm,sr,num_samples_per_feature,receptive_field,input_length)

        if self.opt.concat_outputs:
            # concatenate the song and block input features before returning
            return {'input': torch.cat(input_windowss + [blocks_windows.float()],1), 'target': blocks_targets}
        else:
            # concatenate the song and block input features before returning
            return {'input': input_windowss, 'target': blocks_targets}

    def __len__(self):
        return len(self.audio_files)


def pairwise(iterable):
    "s -> (s0,s1), (s1,s2), (s2, s3), ..."
    a, b = tee(iterable)
    next(b, None)
    return zip(a, b)