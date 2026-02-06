import os
from  elasticsearch import Elasticsearch

es_host = os.getenv("ES_HOST","172.26.219.10")
es_port = int(os.getenv("ES_PORT","9200"))
es_url = os.getenv("ES_URL","http://172.26.219.10:9200")

try:
	es = Elasticsearch([es_url],verify_certs=False)
	if es.ping():
		print("connect success!")
		print("es info:",es.info())
	else:
		print("connect failed!")
except Exception as e:
	print("error!:",e)
