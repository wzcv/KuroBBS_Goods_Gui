#全局变量
import os
token=""
devcode=""
access_token=""
distinct_id=""
headers={}
gameId=0
CorrectProfile= False
base_dir = os.path.abspath(os.path.dirname(__file__))
parent_dir = os.path.dirname(base_dir)
data_dir = os.environ.get('KURO_DATA_DIR', base_dir)
goodslist_path = os.path.join(data_dir, 'goodslist.json')
config_path = os.path.join(data_dir, 'config.json')
tasklistpath = os.path.join(data_dir, 'tasklist.json')
log_path = os.path.join(data_dir, 'log.log')
