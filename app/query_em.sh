curl -X POST "http://172.26.219.10:9200/news_index/_search?pretty" -H "Content-Type: application/json" -d '{
	"query": {
                "multi_match": {
                    "query": "达沃斯",
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

}'
