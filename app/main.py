from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
import os
import json
import uuid
import urllib.parse
from datetime import datetime
import asyncio
import sqlite3
from mcp_api import router as mcp_router, get_mcp_server_details  # Import MCP router and helper
from fastmcp import Client
from fastmcp.client.transports import SSETransport
from dotenv import load_dotenv
from elasticsearch import Elasticsearch


# 检查 .env文件是否存在
if not os.path.exists(".env"):
    raise ValueError("环境变量文件 .env不存在，请检查")

load_dotenv()

# 从env中获取配置
API_KEY = os.getenv("API_KEY")
BASE_URL= os.getenv("BASE_URL")
MODEL_NAME = os.getenv("MODEL_NAME")
 
BOCHAAI_SEARCH_API_KEY = os.getenv("BOCHAAI_SEARCH_API_KEY")

#检查配置是否正确
if not API_KEY or not BASE_URL or not MODEL_NAME :
    raise ValueError("API_KEY配置错误，请检查环境变量 .env文件")

# Initialize FastAPI app
app = FastAPI()

# Include MCP router
app.include_router(mcp_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


 # 初始化AI客户端
ai_client = OpenAI(
    api_key = API_KEY,
    base_url = BASE_URL
)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('chat_history.db')
    cursor = conn.cursor()
    
    # Create chat sessions table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id TEXT PRIMARY KEY,
        summary TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Create messages table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        role TEXT,
        content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES chat_sessions (id)
    )
    ''')
    
    # Create MCP servers table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS mcp_servers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        description TEXT,
        auth_type TEXT,
        auth_value TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Create MCP tools table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS mcp_tools (
        id TEXT PRIMARY KEY,
        server_id TEXT,
        name TEXT NOT NULL,
        description TEXT,
        input_schema TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (server_id) REFERENCES mcp_servers(id)
    )
    ''')
    
    conn.commit()
    conn.close()
    print("数据库初始化完成")

# Perform web search (optional, retained for flexibility)
# https://open.bochaai.com/overview
async def perform_web_search(query: str):
    try:
        import requests
        
        headers = {
            'Content-Type': 'application/json',  # Remove space
            'Authorization': f'Bearer {BOCHAAI_SEARCH_API_KEY}'  # 我加的注释：原来是把查询key放在header里面啊
        }
     
        payload = json.dumps({
            "query": query,
            "freshness": "noLimit",
            "summary": True, 
            "count": 10
        })

        # 使用搜索API, 参考文档 https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
        response = requests.post("https://api.bochaai.com/v1/web-search", headers=headers, data=payload)
        
        # Check status code before parsing JSON
        if response.status_code != 200:
            return f"搜索失败，状态码: {response.status_code}"
            
        # Only parse JSON if status code is 200
        try:
            json_data = response.json()
            #print(f"bbbbbbbbbbbbbb bochaai search response: {json_data}")
            return str(json_data)
        except json.JSONDecodeError as e:
            return f"搜索结果JSON解析失败: {str(e)}"
            
    except Exception as e:
        return f"执行网络搜索时出错: {str(e)}"

async def perform_es_search(query: str):
    try:
        #es_url = os.getenv("ES_URL","http://172.26.219.10:9200")
        es_url = os.getenv("ES_URL")

        es = Elasticsearch([es_url],verify_certs=False)

        if not es.ping():
            return "无法连接到Elasticsearch服务器，请检查服务是否正常运行！"
        
        if not query:
            print("-----ERROR: query parameter is required for ES search!----")
            raise HTTPException(status_code=400, detail="query parameter is required for ES search!")
        
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
        print(f"-----perform_es_search结果: {response_content}")
        return response_content
    except Exception as e:
        print(f"ES搜索时出错: {str(e)}")
        #raise HTTPException(status_code=500, detail=f"ES搜索时出错: {str(e)}")

# Save new chat session
async def create_new_chat_session(session_id: str, query: str, response: str):
    conn = sqlite3.connect('chat_history.db')
    cursor = conn.cursor()
    summary = query[:50] + ("..." if len(query) > 50 else "")
    cursor.execute(
        '''
        INSERT INTO chat_sessions (id, summary, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ''',
        (session_id, summary, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    cursor.execute(
        '''
        INSERT INTO messages (session_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
        ''',
        (session_id, "user", query, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    cursor.execute(
        '''
        INSERT INTO messages (session_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
        ''',
        (session_id, "assistant", response, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()
    conn.close()

# Add message to existing session
async def add_message_to_session(session_id: str, query: str, response: str):
    conn = sqlite3.connect('chat_history.db')
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO messages (session_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
        ''',
        (session_id, "user", query, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    cursor.execute(
        '''
        INSERT INTO messages (session_id, role, content, created_at)
        VALUES (?, ?, ?, ?)
        ''',
        (session_id, "assistant", response, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    cursor.execute(
        '''
        UPDATE chat_sessions
        SET updated_at = ?
        WHERE id = ?
        ''',
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session_id)
    )
    conn.commit()
    conn.close()

# Process stream request (updated to use openai for GLM, requests for tools)
async def process_stream_request(query: str, session_id: str = None, web_search: bool = False, agent_mode: bool = False, es_search: bool = False):
    print(f"-----query: {query}, session_id: {session_id}, web_search: {web_search}, agent_mode: {agent_mode},es_search: {es_search}")
    
    # Initialize database connection
    conn = sqlite3.connect('chat_history.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
    has_session = cursor.fetchone()
    if not has_session:
        session_id = str(uuid.uuid4())

    # Build context (only web search if enabled)
    context_parts = []
    if web_search:
        web_results = await perform_web_search(query)
        context_parts.append(web_results)
    if es_search:
        es_results = await perform_es_search(query)
        context_parts.append(es_results)
    context = "\n".join(context_parts) if context_parts else ""

    # Common response generator function,公用的生成函数，在第340等行被多个StreamingResponse函数调用
    async def generate(content_stream=None, initial_content=""):
        full_response = initial_content
        
        # Handle streaming content if provided
        if content_stream:
            try:
                for chunk in content_stream:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_response += content
                        yield f"data: {json.dumps({'content': content, 'session_id': session_id})}\n\n"
                        await asyncio.sleep(0.01)
                    if chunk.choices[0].finish_reason is not None:
                        yield f"data: {json.dumps({'content': '', 'session_id': session_id, 'done': True})}\n\n"
                        break
            except Exception as e:
                yield f"data: {json.dumps({'content': f'错误：GLM API 请求失败 - {str(e)}', 'session_id': session_id, 'done': True})}\n\n"
                return
        else:
            # For direct response (non-streaming)
            yield f"data: {json.dumps({'content': full_response, 'session_id': session_id})}\n\n"
            yield f"data: {json.dumps({'content': '', 'session_id': session_id, 'done': True})}\n\n"
        
        # Save to database
        if has_session:
            await add_message_to_session(session_id, query, full_response)
        else:
            await create_new_chat_session(session_id, query, full_response)

    # Agent mode: Decide whether to invoke a tool  #我加的：Agent开关就是使用mcp tool的意思
    if agent_mode:  #如果使用mcp工具，则：
        # Fetch available tools
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        #这个简单的联合查询很好理解：
        cursor.execute(" SELECT t.*, s.url FROM mcp_tools t LEFT JOIN mcp_servers s ON t.server_id = s.id ")
        #原来cursor.fetchall0本来也是个数组，它是个二进制，原来通过dict(它)就可以把这个数组里的内容转为字典
        tools = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Construct tool descriptions for the LLM
        tool_descriptions = "\n".join([
            f"server_url: {tool['url']}\n\ntool_name: {tool['name']}\nDescription: {tool['description']}\ninput_schema: {tool['input_schema']}"
            for tool in tools
        ]) if tools else "无可用工具"

        # Prompt to decide tool invocation。如果联网搜索和使用mcp的按钮同时打开了，则会把联网搜索和mcp的结果一起作为上下文信息，--感觉有点不太对？？
        agent_prompt = f"""
        上下文信息:\n{context}\n
        问题: {query}\n
        可用工具:\n{tool_descriptions}\n
        你是一个智能助手，可以根据用户问题选择合适的工具执行操作。
        如果需要使用工具，请返回以下格式的JSON：
        ```json
        {{
          "server_url": "server_url",
          "tool_name": "tool_name",
          "parameters":{{"param_name1": "param_value1", "param_name2": "param_value2"}}
        }}
        ```
        如果不需要工具，直接返回回答内容的字符串。
        """

        # Call GLM API using openai (non-streaming)
        try:
            response = ai_client.chat.completions.create(
                model = MODEL_NAME,
                messages= [
                    {"role": "system", "content": "你是一个智能助手，擅长选择合适的工具或直接回答问题。"},
                    {"role": "user", "content": agent_prompt}
                ],
                stream=False,
                response_format={"type": "json_object"} 
            )
            decision = response.choices[0].message.content.strip()
            print("*****decision:*****",decision)  # 我加的，预测是一个json的工具列表
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"GLM API request failed: {str(e)}")

        try:
            # Check if the response is a tool invocation
            decision_json = json.loads(decision)
            if "server_url" in decision_json and "tool_name" in decision_json:
                server_url = decision_json["server_url"]
                tool_name = decision_json["tool_name"]
                parameters = decision_json["parameters"]
                print("*****decision_json:*****",decision_json)
                
                try:
                    async with Client(SSETransport(server_url)) as client:    ##我加的，原来是在这里调用的mcp tool ！！发这个课件的时候，下一节课才讲这里怎么调用mcp tool的细节
                        try:
                            tool_result = await client.call_tool(tool_name, parameters)
                        except Exception as tool_error:
                            print(f"@@@@@@Error in call_tool:{tool_error}")  #我加的，找到了是因为db的表不存在的问题，哈哈
                        tool_response = f"工具 {tool_name} 的执行结果：{tool_result}"
                        print(f"#################工具 {tool_name} 的执行结果：{tool_result}")
                        
                        # 继续调用大模型
                        prompt = f"上下文信息:\n{tool_result}\n\n问题: {query}\n请基于上下文信息回答问题:"
                        stream = ai_client.chat.completions.create(
                            model=MODEL_NAME,
                            ####model="gpt-4o",
                            messages=[{"role": "user", "content": prompt}],
                            stream=True
                        )
                        # Use the common generator with the stream and initial content
                        return StreamingResponse(
                            generate(stream, tool_response),
                            media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Transfer-Encoding": "chunked"}
                        )
                except Exception as e:
                    return StreamingResponse(
                        generate(initial_content=f"工具 {tool_name} 执行失败：{str(e)}"),
                        media_type="text/event-stream",
                        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Transfer-Encoding": "chunked"}
                    )
            else:
                # Direct response from decision
                return StreamingResponse(
                    generate(initial_content=decision),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Transfer-Encoding": "chunked"}
                )
        except json.JSONDecodeError:
            # If not JSON, treat as direct response
            return StreamingResponse(
                generate(initial_content=decision),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Transfer-Encoding": "chunked"}
            )
    
    # Non-agent mode: Streaming response
    prompt = f"上下文信息:\n{context}\n\n问题: {query}\n请基于上下文信息回答问题，如果上下文中没有相关信息，请回答我们的资源库中没有相关信息，不要编造答案。"
    print(f"pppppppppppp prompt: {prompt}")
    print(f"-----------------------:,{MODEL_NAME}")
    
    try:
        stream = ai_client.chat.completions.create(
            model=MODEL_NAME,
            #model="gpt-4o",
            messages=[
                {"role": "system", "content": "你是一个专业的问答助手。"},
                {"role": "user", "content": prompt}
            ],
            stream=True
        )
    except Exception as e:
        async def generate_error():
            global e
            yield f"data: {json.dumps({'content': f'错误：大模型 API 请求失败 - {e}', 'session_id': session_id, 'done': True})}\n\n"
        return StreamingResponse(
            generate_error(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Transfer-Encoding": "chunked"}
        )
    
    # Use the common generator with the stream
    return StreamingResponse(
        generate(stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "Transfer-Encoding": "chunked"}
    )


# Stream endpoint
@app.get("/api/stream")
async def stream(
    query: str,
    session_id: str = Query(None),  # 我加的：可以是空的，因为可能是新的对话，
    web_search: bool = Query(False),
    agent_mode: bool = Query(False),
    es_search: bool = Query(False),
):
    return await process_stream_request(query, session_id, web_search, agent_mode, es_search)


# 会话历史记录 API
@app.get("/api/chat/history")
async def get_chat_history():
    try:
        conn = sqlite3.connect('chat_history.db')
        conn.row_factory = sqlite3.Row  # 启用行工厂，使结果可以通过列名访问
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, summary, updated_at  FROM chat_sessions ORDER BY updated_at DESC")
        rows = cursor.fetchall()
        
        # 将行转换为字典
        sessions = [dict(row) for row in rows]
        
        conn.close()
        return sessions
        
    except Exception as e:
        print(f"获取聊天历史失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取聊天历史失败: {str(e)}")

@app.get("/api/chat/session/{session_id}")
async def get_session(session_id: str):
    try:
        conn = sqlite3.connect('chat_history.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 查询会话是否存在
        cursor.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
        session = cursor.fetchone()
        
        if not session:
            conn.close()
            raise HTTPException(status_code=404, detail="会话不存在")
        
        # 获取会话中的所有消息
        cursor.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id asc",
            (session_id,)
        )
        messages = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return {"messages": messages}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"获取会话详情失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取会话详情失败: {str(e)}")

# 删除会话
@app.delete("/api/chat/session/{session_id}")
async def delete_session(session_id: str):
    try:
        conn = sqlite3.connect('chat_history.db')
        cursor = conn.cursor()
        
        # 首先删除会话关联的所有消息
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        
        # 然后删除会话本身
        cursor.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="会话不存在")
        
        conn.commit()
        conn.close()
        
        return {"message": "会话已删除"}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"删除会话失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"删除会话失败: {str(e)}")



# 导出会话为markdown格式下载
@app.get("/api/chat/export/{session_id}")
async def export_session(session_id: str):
    try:
        conn = sqlite3.connect('chat_history.db')
        conn.row_factory = sqlite3.Row  # Set row factory to enable dictionary access
        cursor = conn.cursor()
        
        # 查询会话是否存在
        cursor.execute("SELECT id, summary FROM chat_sessions WHERE id = ?", (session_id,))
        session = cursor.fetchone()
        
        if not session:
            conn.close()
            raise HTTPException(status_code=404, detail="会话不存在")
        
        # 获取会话中的所有消息
        cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id asc", (session_id,))
        messages = cursor.fetchall()
        
        # 构建markdown内容
        markdown_content = f"# 会话历史记录\n\n"
        markdown_content += f"## 会话ID: {session_id}\n\n"
        markdown_content += f"## 会话总结: {session['summary']}\n\n"
        
        for message in messages:
            role = message['role']
            content = message['content']
            markdown_content += f"### {role}\n\n{content}\n\n"
        
        conn.close()
        
        return StreamingResponse(
            iter([markdown_content]), 
            media_type="text/markdown", 
            headers={"Content-Disposition": f"attachment; filename=session_{session_id}.md"}
        )
        
    except Exception as e:
        print(f"导出会话失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"导出会话失败: {str(e)}")


# 健康检查接口
@app.get("/api/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    init_db()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
