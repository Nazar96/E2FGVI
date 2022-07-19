# -*- coding: utf-8 -*-
import cv2
from PIL import Image
import numpy as np
import importlib
import os
import argparse
import torch
import sys

from core.utils import to_tensors

from pathlib import Path
from typing import Union
import logging.config

parser = argparse.ArgumentParser(description="E2FGVI")
parser.add_argument("-v", "--video", type=str, required=True)
parser.add_argument("-c", "--ckpt", type=str, required=True)
parser.add_argument("-m", "--mask", type=str, required=True)
parser.add_argument("--model", type=str, choices=['e2fgvi', 'e2fgvi_hq'])
parser.add_argument("--step", type=int, default=10)
parser.add_argument("--num_ref", type=int, default=-1)
parser.add_argument("--neighbor_stride", type=int, default=5)
parser.add_argument("--savefps", type=int, default=24)

# args for e2fgvi_hq (which can handle videos with arbitrary resolution)
parser.add_argument("--set_size", action='store_true', default=False)
parser.add_argument("--width", type=int)
parser.add_argument("--height", type=int)

args = parser.parse_args()

ref_length = args.step  # ref_step
num_ref = args.num_ref
neighbor_stride = args.neighbor_stride
default_fps = args.savefps


def get_dict_config(log_filename: Union[str, Path] = "logfile.log"):
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)s in %(name)s (line: %(lineno)d) - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "simple": {
                "format": "%(message)s",
            },
        },
        "handlers": {
            "logfile": {
                "formatter": "default",
                "level": "DEBUG",  # FILTER: All logs
                "class": "logging.handlers.RotatingFileHandler",
                # Params for class above:
                "filename": log_filename,
                "mode": "w",
                "encoding": "utf-8",
            },
            "verbose_output": {
                "formatter": "default",
                "level": "INFO",  # FILTER: Only ERROR and CRITICAL logs
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {
            "": {   # root logger
                "level": "DEBUG",
                "handlers": [
                    "verbose_output"
                ]
            }
            # "__main__": {  # The name of the logger, this SHOULD match the module!
            #     "level": "INFO",  # FILTER: only INFO logs and above from "__main__" logger
            #     "handlers": [
            #         "verbose_output",  # Refer the handler defined above
            #     ],
            # },
        },
    }


logging.config.dictConfig(get_dict_config(Path("test.log")))
logger = logging.getLogger(__name__)

# handler = logging.StreamHandler(sys.stdout)
# logger.addHandler(handler)


# sample reference frames from the whole video
def get_ref_index(f, neighbor_ids, length):
    ref_index = []
    if num_ref == -1:
        for i in range(0, length, ref_length):
            if i not in neighbor_ids:
                ref_index.append(i)
    else:
        start_idx = max(0, f - ref_length * (num_ref // 2))
        end_idx = min(length, f + ref_length * (num_ref // 2))
        for i in range(start_idx, end_idx + 1, ref_length):
            if i not in neighbor_ids:
                if len(ref_index) > num_ref:
                    break
                ref_index.append(i)
    return ref_index


# read frame-wise masks
def read_mask(mpath, size):
    masks = []
    mnames = os.listdir(mpath)
    mnames.sort()
    for mp in mnames:
        m = Image.open(os.path.join(mpath, mp))
        m = m.resize(size, Image.NEAREST)
        m = np.array(m.convert('L'))
        m = np.array(m > 0).astype(np.uint8)
        m = cv2.dilate(m,
                       cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)),
                       iterations=4)
        masks.append(Image.fromarray(m * 255))
    return masks


#  read frames from video
def read_frame_from_videos(args):
    vname = args.video
    frames = []
    if args.use_mp4:
        vidcap = cv2.VideoCapture(vname)
        success, image = vidcap.read()
        count = 0
        while success:
            image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            frames.append(image)
            success, image = vidcap.read()
            count += 1
    else:
        lst = os.listdir(vname)
        lst.sort()
        fr_lst = [vname + '/' + name for name in lst]
        for fr in fr_lst:
            image = cv2.imread(fr)
            image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            frames.append(image)
    return frames


# resize frames
def resize_frames(frames, size=None):
    if size is not None:
        frames = [f.resize(size) for f in frames]
    else:
        size = frames[0].size
    return frames, size


def main_worker():
    # set up models
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if args.model == "e2fgvi":
        size = (432, 240)
    elif args.set_size:
        size = (args.width, args.height)
    else:
        size = None

    net = importlib.import_module('model.' + args.model)
    model = net.InpaintGenerator().to(device)
    data = torch.load(args.ckpt, map_location=device)
    model.load_state_dict(data)
    model.eval()

    # prepare datset
    args.use_mp4 = True if args.video.endswith('.mp4') else False
    frames = read_frame_from_videos(args)
    frames, size = resize_frames(frames, size)
    h, w = size[1], size[0]
    video_length = len(frames)
    imgs = to_tensors()(frames).unsqueeze(0) * 2 - 1
    frames = [np.array(f).astype(np.uint8) for f in frames]

    masks = read_mask(args.mask, size)
    binary_masks = [
        np.expand_dims((np.array(m) != 0).astype(np.uint8), 2) for m in masks
    ]
    masks = to_tensors()(masks).unsqueeze(0)
    imgs, masks = imgs.to(device), masks.to(device)
    comp_frames = [None] * video_length

    # completing holes by e2fgvi
    for f in range(0, video_length, neighbor_stride):
        logger.info(f"frames {f}/{video_length}")
        
        neighbor_ids = [
            i for i in range(max(0, f - neighbor_stride),
                             min(video_length, f + neighbor_stride + 1))
        ]
        ref_ids = get_ref_index(f, neighbor_ids, video_length)
        selected_imgs = imgs[:1, neighbor_ids + ref_ids, :, :, :]
        selected_masks = masks[:1, neighbor_ids + ref_ids, :, :, :]
        with torch.no_grad():
            masked_imgs = selected_imgs * (1 - selected_masks)
            mod_size_h = 60
            mod_size_w = 108
            h_pad = (mod_size_h - h % mod_size_h) % mod_size_h
            w_pad = (mod_size_w - w % mod_size_w) % mod_size_w
            masked_imgs = torch.cat(
                [masked_imgs, torch.flip(masked_imgs, [3])],
                3)[:, :, :, :h + h_pad, :]
            masked_imgs = torch.cat(
                [masked_imgs, torch.flip(masked_imgs, [4])],
                4)[:, :, :, :, :w + w_pad]
            pred_imgs, _ = model(masked_imgs, len(neighbor_ids))
            pred_imgs = pred_imgs[:, :, :h, :w]
            pred_imgs = (pred_imgs + 1) / 2
            pred_imgs = pred_imgs.cpu().permute(0, 2, 3, 1).numpy() * 255
            for i in range(len(neighbor_ids)):
                idx = neighbor_ids[i]
                img = np.array(pred_imgs[i]).astype(
                    np.uint8) * binary_masks[idx] + frames[idx] * (
                        1 - binary_masks[idx])
                if comp_frames[idx] is None:
                    comp_frames[idx] = img
                else:
                    comp_frames[idx] = comp_frames[idx].astype(
                        np.float32) * 0.5 + img.astype(np.float32) * 0.5

    # saving videos
    save_dir_name = '/result/'
    ext_name = '_results.mp4'
    save_base_name = args.video.split('/')[-1]
    save_name = save_base_name.replace(
        '.mp4', ext_name) if args.use_mp4 else save_base_name + ext_name
    if not os.path.exists(save_dir_name):
        os.makedirs(save_dir_name)
    save_path = os.path.join(save_dir_name, save_name)

    for f in range(video_length):
        comp = comp_frames[f].astype(np.uint8)
        cv2.imwrite(f'/result/{f}.png', comp)


if __name__ == '__main__':
    main_worker()
