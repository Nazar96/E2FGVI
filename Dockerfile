FROM pytorch/pytorch:1.5.1-cuda10.1-cudnn7-runtime
LABEL Name=e2fgvi Version=0.0.1

RUN apt-get update && apt-get install ffmpeg libsm6 libxext6 git -y &&\
    git clone https://github.com/Nazar96/E2FGVI.git

WORKDIR /workspace/E2FGVI/

RUN pip install opencv-python==4.5.5.64 &&\
    pip install gdown==4.5.1 &&\
    gdown --output release_model/ 10wGdKSUOie0XmCr8SQ2A2FeDe-mfn5w3 &&\
    pip install mmcv-full==1.4.8 -f https://download.openmmlab.com/mmcv/dist/cu101/torch1.5.0/index.html
