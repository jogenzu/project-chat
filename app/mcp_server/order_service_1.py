import sqlite3

# 数据库连接
conn = sqlite3.connect('../chat_history.db')
cursor = conn.cursor()


query = """
SELECT customer_name, SUM(price) as total_consumption
FROM orders
GROUP BY customer_name
ORDER BY total_consumption DESC
LIMIT 1
"""
cursor.execute(query)
result = cursor.fetchone()
if result is None:
    print("未找到客户记录")
print(f"消费最高的用户：{result[0]}，总消费额 {result[1]}")

