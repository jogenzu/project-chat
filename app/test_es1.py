import os
from  elasticsearch import Elasticsearch
import json
def perform_es_search(query):
    try:
        #es_host = os.getenv("ES_HOST","172.26.219.10")
        #es_port = int(os.getenv("ES_PORT","9200"))

        #es = Elasticsearch([{'host': es_host, 'port': es_port}])

        #es_url = os.getenv("ES_URL","http://172.26.219.10:9200")
        es_url = os.getenv("ES_URL")

        es = Elasticsearch([es_url],verify_certs=False)


        if not es.ping():
            return "-----can not connect!---"
        
        if not query:
            print("-----ERROR: query parameter is required for ES search!----")
            
        
        search_body = {
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["title", "content"],
                    "type": "best_fields" 
                }
            },
            "highlight": {
                "pre_tags": ["<em>"],
                "post_tags": ["</em>"],
                "fields": {
                    "title": {},
                    "content": {}
                }
            },
            "size": 10,
            "_source": ["title", "content"]
        }
        
        response = es.search(index="news_index", body=search_body).body
        response_content = json.dumps(response, ensure_ascii=False)
        print("-----perform_es_search:",response_content)
        return response_content
    except Exception as e:
        print("----:",str(e))
    
if __name__ == "__main__":
    es_results =  perform_es_search("AI")
    print(es_results)
