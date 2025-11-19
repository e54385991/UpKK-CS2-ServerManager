#!/usr/bin/env python3
"""
CS2 Server Manager 使用示例
演示如何通过 API 管理服务器
"""
import json

API_BASE_URL = "http://localhost:8000"


def example_usage():
    """演示 API 使用"""
    
    print("=" * 60)
    print("CS2 Server Manager - API 使用示例")
    print("=" * 60)
    print("\n注意: 确保已启动 API 服务器 (python main.py)")
    print("以下是示例请求，需要实际运行时替换为真实数据\n")
    
    # 示例 1: 创建服务器
    print("1. 创建服务器")
    print("-" * 60)
    server_data = {
        "name": "CS2-Server-01",
        "host": "192.168.1.100",
        "ssh_port": 22,
        "ssh_user": "ubuntu",
        "auth_type": "password",
        "ssh_password": "your_password",
        "game_port": 27015,
        "game_directory": "/home/cs2server/cs2",
        "description": "Primary CS2 Server"
    }
    print(f"POST {API_BASE_URL}/servers")
    print(f"Data: {json.dumps(server_data, indent=2)}")
    print()
    
    # 示例 2: 列出所有服务器
    print("2. 列出所有服务器")
    print("-" * 60)
    print(f"GET {API_BASE_URL}/servers")
    print()
    
    # 示例 3: 获取特定服务器
    print("3. 获取特定服务器")
    print("-" * 60)
    server_id = 1
    print(f"GET {API_BASE_URL}/servers/{server_id}")
    print()
    
    # 示例 4: 部署服务器
    print("4. 部署 CS2 服务器")
    print("-" * 60)
    print(f"POST {API_BASE_URL}/servers/{server_id}/actions")
    print(f'Data: {{"action": "deploy"}}')
    print()
    
    # 示例 5: 启动服务器
    print("5. 启动服务器")
    print("-" * 60)
    print(f"POST {API_BASE_URL}/servers/{server_id}/actions")
    print(f'Data: {{"action": "start"}}')
    print()
    
    # 示例 6: 检查服务器状态
    print("6. 检查服务器状态")
    print("-" * 60)
    print(f"POST {API_BASE_URL}/servers/{server_id}/actions")
    print(f'Data: {{"action": "status"}}')
    print()
    
    # 示例 7: 停止服务器
    print("7. 停止服务器")
    print("-" * 60)
    print(f"POST {API_BASE_URL}/servers/{server_id}/actions")
    print(f'Data: {{"action": "stop"}}')
    print()
    
    # 示例 8: 重启服务器
    print("8. 重启服务器")
    print("-" * 60)
    print(f"POST {API_BASE_URL}/servers/{server_id}/actions")
    print(f'Data: {{"action": "restart"}}')
    print()
    
    # 示例 9: 查看部署日志
    print("9. 查看部署日志")
    print("-" * 60)
    print(f"GET {API_BASE_URL}/servers/{server_id}/logs")
    print()
    
    # 示例 10: 更新服务器信息
    print("10. 更新服务器信息")
    print("-" * 60)
    update_data = {
        "description": "Updated description"
    }
    print(f"PUT {API_BASE_URL}/servers/{server_id}")
    print(f"Data: {json.dumps(update_data, indent=2)}")
    print()
    
    # 示例 11: 删除服务器
    print("11. 删除服务器")
    print("-" * 60)
    print(f"DELETE {API_BASE_URL}/servers/{server_id}")
    print()
    
    print("=" * 60)
    print("使用 curl 命令示例:")
    print("=" * 60)
    print()
    
    # curl 示例
    print("# 健康检查")
    print(f"curl -X GET {API_BASE_URL}/health")
    print()
    
    print("# 创建服务器")
    print(f'''curl -X POST {API_BASE_URL}/servers \\
  -H "Content-Type: application/json" \\
  -d '{json.dumps(server_data)}'
''')
    
    print("# 部署服务器")
    print(f'''curl -X POST {API_BASE_URL}/servers/1/actions \\
  -H "Content-Type: application/json" \\
  -d '{{"action": "deploy"}}'
''')
    
    print("=" * 60)
    print("访问 API 文档:")
    print(f"  Swagger UI: {API_BASE_URL}/docs")
    print(f"  ReDoc:      {API_BASE_URL}/redoc")
    print("=" * 60)


if __name__ == "__main__":
    example_usage()
