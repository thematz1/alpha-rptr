# coding: UTF-8

import base64
import logging
import os
import time
import uuid
from datetime import timedelta

import numpy as np
import pandas as pd
from pandas import Series
import requests
from bravado.exception import HTTPError

from src.config import config as conf


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


allowed_range = {
    "1m": ["1m", "1T", 1, 1], "2m":  ["1m", "2T", 2, 2], "3m":  ["1m", "3T", 3, 3],
    "4m": ["1m", "4T", 4, 4], "5m": ["1m", "5T", 5, 5], "6m": ["1m", "6T", 6, 6],
    "7m": ["1m", "7T", 7, 7], "8m": ["1m", "8T", 8, 8], "9m": ["1m", "9T", 9, 9],
    "10m": ["1m", "10T", 10, 10], "11m": ["1m", "11T", 11, 11],
    "15m": ["5m", "15T", 3, 15], "30m": ["5m", "30T", 6, 30], "45m": ["5m", "45T", 9, 45],
    "1h": ["1h", "1H", 1, 1], "2h":  ["1h", "2H", 2, 2],
    "3h": ["1h", "3H", 3, 3], "4h":  ["1h", "4H", 4, 4],
    "6h": ["1h", "6H", 6, 6], "12h": ["1h", "12H", 12, 12],
    "1d": ["1d", "1D", 1, 1], "3d": ["3d", "3D", 3, 3]
    # not support yet '1w', '2w', '1M'
}


allowed_range_minute_granularity = {
    "1m": ["1m", "1T", 1, 1], "2m":  ["1m", "2T", 2, 2], "3m":  ["1m", "3T", 3, 3],
    "4m": ["1m", "4T", 4, 4], "5m": ["1m", "5T", 5, 5], "6m": ["1m", "6T", 6, 6],
    "7m": ["1m", "7T", 7, 7], "8m": ["1m", "8T", 8, 8], "9m": ["1m", "9T", 9, 9],
    "10m": ["1m", "10T", 10, 10], "11m": ["1m", "11T", 11, 11],
    "15m": ["1m", "15T", 15, 15], "30m": ["1m", "30T", 30, 30], "45m": ["1m", "45T", 45, 45],
    "1h": ["1m", "60T", 60, 60], "2h":  ["1m", "120T", 120, 120],
    "3h": ["1m", "180T", 180, 180], "4h":  ["1m", "240T", 240, 240],
    "6h": ["1m", "360T", 360, 360], "12h": ["1m", "720T", 720, 720],
    "1d": ["1m", "1440T", 1440, 1440], "3d": ["1m", "4320T", 4320, 4320]
    # not support yet '1w', '2w', '1M'
}


class FatalError(Exception):
    pass


def find_timeframe_string(timeframe_in_minutes):
    """
    finds and returns tf string based on minute count
    """
    for tf, value in allowed_range_minute_granularity.items(): 
        if value[3] == timeframe_in_minutes:
            return tf
            

def ord_suffix():
    return "_" + uuid.uuid4().hex[:15]


def load_data(file):
    """
    Read data from a file.
    """    
    source = pd.read_csv(file)
    # data_frame = pd.DataFrame({
    #     'timestamp': pd.to_datetime(source['timestamp']),
    #     'open': source['open'],
    #     'close': source['close'],
    #     'high': source['high'],
    #     'low': source['low'],
    #     'volume': source['volume']
    # })
    # data_frame = data_frame.set_index('timestamp')
    # return data_frame.tz_localize(None).tz_localize('UTC', level=0)
    return source


def delta(bin_size='1h', minute_granularity= False):
    if minute_granularity:
        return timedelta(minutes= allowed_range_minute_granularity[bin_size][3])
    elif bin_size.endswith('d'):
        return timedelta(days=allowed_range[bin_size][3])
    elif bin_size.endswith('h'):
        return timedelta(hours=allowed_range[bin_size][3])
    elif bin_size.endswith('m'):
        return timedelta(minutes=allowed_range[bin_size][3])


def validate_continuous(data, bin_size):    
    last_date = None
    for i in range(len(data)):
        index = data.iloc[-1 * (i + 1)].name
        if last_date is None:
            last_date = index
            continue
        if last_date - index != delta(bin_size):
            return False, index
        last_date = index
    return True, None


def verify_series(series: Series, min_length: int = None) -> Series:
    """
    If a Pandas Series and it meets the min_length of the indicator return it.
    """
    has_length = min_length is not None and isinstance(min_length, int)
    if series is not None and isinstance(series, Series):
        return None if has_length and series.size < min_length else series


def to_data_frame(data):    
    data_frame = pd.DataFrame(data, columns=["timestamp", "high", "low", "open", "close", "volume"])    
    data_frame = data_frame.set_index("timestamp")
    data_frame = data_frame.tz_localize(None).tz_localize('UTC', level=0)    
    return data_frame


def resample(data_frame, bin_size, minute_granularity=False, label="right", closed="right"):      
    resample_time = allowed_range_minute_granularity[bin_size][1] if minute_granularity else allowed_range[bin_size][1]
    return data_frame.resample(resample_time, label=label, closed=closed).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })   


def retry(func, count=5):
    err = None
    for i in range(count):
        try:           
            ret, res = func()            
            rate_limit = int(res.headers['X-RateLimit-Limit'])
            rate_remain = int(res.headers['X-RateLimit-Remaining'])
            if rate_remain < 10:
                time.sleep(5 * 60 * (1 + rate_limit - rate_remain) / rate_limit)
            return ret
        except HTTPError as error:
            status_code = error.status_code
            err = error
            if status_code >= 500:
                time.sleep(pow(2, i + 1))
                continue
            elif status_code == 400 or \
                    status_code == 401 or \
                    status_code == 402 or \
                    status_code == 403 or \
                    status_code == 404 or \
                    status_code == 429:
                raise FatalError(error)
    raise err


def retry_binance_futures(func, count=5):
    err = None
    for i in range(count):
        try:            
            ret, res = func()

            #res_header = res.headers['X-MBX-USED-WEIGHT-1M']                   
            rate_limit = int(res.headers['X-MBX-USED-WEIGHT-1M'])
            #todo finish retry limit and status codes
            
            # rate_remain = None
            # try:                
            #     rate_remain = int(res.headers['X-MBX-ORDER-COUNT-1M'])
            # except KeyError:             #             
            #     #return ret
            #     pass
            # if rate_remain is not None and rate_remain < 10:
            #     time.sleep(5 * 60 * (1 + rate_limit - rate_remain) / rate_limit)
            return ret
        except HTTPError as error:
            status_code = error.status_code
            err = error
            if status_code >= 500:
                time.sleep(pow(2, i + 1))
                continue
            elif status_code == 400 or \
                    status_code == 401 or \
                    status_code == 402 or \
                    status_code == 403 or \
                    status_code == 404 or \
                    status_code == 429:
                raise FatalError(error)
    raise err


class Side:
    Long = "Long"
    Short = "Short"
    Close = "Close"
    Unknown = "Unknown"


def notify(message: object, fileName: object = None) -> object:
    url = 'https://notify-api.line.me/api/notify'
    #api_key = os.environ.get('LINE_APIKEY')
    api_key = conf['line_apikey']['API_KEY']
    if api_key is None or len(api_key) == 0:
        return

    payload = {'message': message}
    headers = {'Authorization': 'Bearer ' + api_key}
    if fileName is None:
        try:
            requests.post(url, data=payload, headers=headers)
        except:
            pass
    else:
        try:
            files = {"imageFile": open(fileName, "rb")}
            requests.post(url, data=payload, headers=headers, files=files)
        except:
            pass