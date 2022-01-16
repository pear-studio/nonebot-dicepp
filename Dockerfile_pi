FROM python:3.8

RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple

WORKDIR /DicePP

COPY ./requirements.txt /DicePP/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /DicePP/requirements.txt

COPY ./ /DicePP

CMD ["python", "/DicePP/bot.py"]