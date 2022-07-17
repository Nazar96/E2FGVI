FROM pytorch/pytorch:1.5.1-cuda10.1-cudnn7-runtime
LABEL Name=e2fgvi Version=0.0.1

COPY test.py test.py
COPY model model/
COPY core core/

RUN apt update && apt install ffmpeg libsm6 libxext6 -y &&\
    pip install opencv-python==4.5.5.64 &&\
    pip install gdown==4.5.1 &&\
    mkdir release_model/ &&\
    gdown --output release_model/ 10wGdKSUOie0XmCr8SQ2A2FeDe-mfn5w3 &&\
    pip install mmcv-full==1.4.8 -f https://download.openmmlab.com/mmcv/dist/cu101/torch1.5.0/index.html &&\
    rm -rf /var/lib/apt/lists/* &&\
    mkdir /result /data

ENTRYPOINT python test.py --model e2fgvi_hq --video /data/frames --mask /data/masks  --ckpt release_model/E2FGVI-HQ-CVPR22.pth
