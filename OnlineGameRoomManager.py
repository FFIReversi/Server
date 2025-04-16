import asyncio
import websockets
import json
import logging

# 設置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 房間狀態
rooms = {}
# 客戶端對應的房間
client_rooms = {}

# 定義消息類型常量
MESSAGE_TYPE = {
    "SUCCESS": "success",  # 成功操作
    "ERROR": "error",  # 錯誤
    "ROOM_CREATED": "room_created",  # 房間已創建
    "ROOM_JOINED": "room_joined",  # 加入房間
    "ROOM_FULL": "room_full",  # 加入已滿
    "PLAYER_JOINED": "player_joined",  # 玩家加入通知
    "PLAYER_LEFT": "player_left",  # 玩家離開通知
    "DATA_RECEIVED": "data_received",  # 數據接收確認
    "DATA_TRANSFER": "data_transfer"  # 從其他玩家轉發的數據
}


async def handler(websocket):
    logger.info(f"新的連接已建立: {id(websocket)}")

    try:
        async for message in websocket:
            logger.info(f"收到原始消息: {message}")

            try:
                data = json.loads(message)
                logger.info(f"解析後的數據: {data}")
            except json.JSONDecodeError:
                logger.error(f"無效的 JSON 格式: {message}")
                await websocket.send(json.dumps({
                    "status": MESSAGE_TYPE["ERROR"],
                    "message": "Invalid data format. Please provide correct JSON",
                    "data": None
                }))
                continue

            if not isinstance(data, dict) or "room_id" not in data:
                logger.warning("缺少必要參數 room_id")
                await websocket.send(json.dumps({
                    "status": MESSAGE_TYPE["ERROR"],
                    "message": "Missing required parameter: room_id",
                    "data": None
                }))
                continue

            room_id = data.get("room_id")

            logger.info(f"處理房間 {room_id} 的消息")

            # 處理創建房間
            if room_id not in rooms:
                rooms[room_id] = {
                    "clients": [websocket],
                    "players": {websocket: 1},
                    "used_player_numbers": {1: True}  # 記錄已使用的玩家編號
                }
                client_rooms[websocket] = room_id

                await websocket.send(json.dumps({
                    "status": MESSAGE_TYPE["ROOM_CREATED"],
                    "message": f"Room {room_id} has been created. You are player 1",
                    "data": {
                        "room_id": room_id,
                        "player_number": 1,
                        "current_players_count": 1,  # 添加當前玩家數量
                        "max_players": 2  # 添加最大玩家數量
                    }
                }))
                logger.info(f"玩家 1 創建了房間 {room_id}")
                continue

            # 處理加入房間
            room = rooms[room_id]
            if websocket not in room["clients"]:
                if len(room["clients"]) >= 2:
                    await websocket.send(json.dumps({
                        "status": MESSAGE_TYPE["ROOM_FULL"],
                        "message": "Room is full. Cannot join",
                        "data": {
                            "room_id": room_id,
                            "current_players_count": len(room["clients"]),  # 添加當前玩家數量
                            "max_players": 2  # 添加最大玩家數量
                        }
                    }))
                    logger.info(f"有玩家嘗試加入房間 {room_id}，但房間已滿")
                    continue

                # 查找可用的玩家編號 (優先使用最小可用編號)
                available_numbers = [i for i in range(1, 3) if
                                     i not in [room["players"][client] for client in room["clients"]]]
                player_number = min(available_numbers) if available_numbers else 2

                # 更新房間狀態
                room["clients"].append(websocket)
                room["players"][websocket] = player_number
                room["used_player_numbers"] = room["used_player_numbers"] if "used_player_numbers" in room else {}
                room["used_player_numbers"][player_number] = True
                client_rooms[websocket] = room_id

                # 獲取所有玩家編號列表
                players_list = list(room["players"].values())
                current_players_count = len(room["clients"])

                # 通知加入者
                await websocket.send(json.dumps({
                    "status": MESSAGE_TYPE["ROOM_JOINED"],
                    "message": f"You have joined room {room_id}. You are player {player_number}",
                    "data": {
                        "room_id": room_id,
                        "player_number": player_number,
                        "current_players_count": current_players_count,  # 添加當前玩家數量
                        "max_players": 2,  # 添加最大玩家數量
                        "players": players_list  # 添加玩家列表
                    }
                }))
                logger.info(f"玩家 {player_number} 加入了房間 {room_id}")

                # 通知其他玩家
                for client in room["clients"]:
                    if client != websocket:
                        try:
                            await client.send(json.dumps({
                                "status": MESSAGE_TYPE["PLAYER_JOINED"],
                                "message": f"Player {player_number} has joined the room",
                                "data": {
                                    "room_id": room_id,
                                    "player_number": player_number,
                                    "current_players_count": current_players_count,  # 添加當前玩家數量
                                    "players": players_list  # 添加玩家列表
                                }
                            }))
                            logger.info(f"通知玩家 {room['players'][client]} 有新玩家加入")
                        except Exception as e:
                            logger.error(f"通知失敗: {e}")

            # 轉發玩家數據
            else:  # 玩家已在房間中
                player_number = room["players"][websocket]

                # 轉發原始數據，但使用統一結構
                original_data = data.copy()  # 保存原始數據副本

                # 轉發給其他玩家
                forward_count = 0
                for client in list(room["clients"]):
                    if client == websocket:  # 不發給自己
                        continue

                    try:
                        # 包裝數據到統一格式
                        forward_msg = {
                            "status": MESSAGE_TYPE["DATA_TRANSFER"],
                            "message": "Received data from another player",
                            "data": original_data,
                            "from_player": player_number
                        }

                        json_msg = json.dumps(forward_msg)
                        logger.info(f"準備發送數據: {json_msg}")
                        await client.send(json_msg)
                        forward_count += 1
                        logger.info(f"已轉發數據給玩家 {room['players'][client]}")
                    except websockets.exceptions.ConnectionClosed:
                        logger.warning(f"客戶端已斷開連接，無法發送訊息，準備清理")
                        await cleanup_player(client)
                    except Exception as e:
                        logger.error(f"轉發數據時發生錯誤: {e}")

                # 確認轉發狀態
                if forward_count == 0:
                    logger.warning(f"房間 {room_id} 中沒有其他玩家，數據未轉發")

                # 回覆發送者數據已接收
                await websocket.send(json.dumps({
                    "status": MESSAGE_TYPE["DATA_RECEIVED"],
                    "message": "Data received and forwarded",
                    "data": {
                        "room_id": room_id,
                        "recipients_count": forward_count,
                        "current_players_count": len(room["clients"])  # 添加當前玩家數量
                    }
                }))

    except websockets.exceptions.ConnectionClosed as e:
        logger.info(f"連接關閉: {e}, 客戶端: {id(websocket)}")
    except Exception as e:
        logger.error(f"處理消息時發生錯誤: {str(e)}")
    finally:
        # 確保斷線時清理玩家
        await cleanup_player(websocket)
        logger.info(f"連接已清理: {id(websocket)}")


async def cleanup_player(websocket):
    """清理斷線的玩家"""
    logger.info(f"開始清理斷線的玩家: {id(websocket)}")

    # 查找此客戶端在哪個房間
    room_id = client_rooms.pop(websocket, None)
    if room_id is None:
        logger.info(f"客戶端不在任何房間中: {id(websocket)}")
        return

    room = rooms.get(room_id)
    if room is None:
        logger.info(f"房間 {room_id} 不存在")
        return

    if websocket in room["clients"]:
        player_number = room["players"].pop(websocket, None)
        # 釋放玩家編號
        if "used_player_numbers" in room and player_number in room["used_player_numbers"]:
            del room["used_player_numbers"][player_number]

        room["clients"].remove(websocket)
        logger.info(f"玩家 {player_number} 已從房間 {room_id} 中移除")

        # 獲取更新後的玩家列表和數量
        players_list = list(room["players"].values())
        current_players_count = len(room["clients"])

        # 通知房間內其他玩家
        for client in list(room["clients"]):
            try:
                await client.send(json.dumps({
                    "status": MESSAGE_TYPE["PLAYER_LEFT"],
                    "message": f"Player {player_number} has left room {room_id}",
                    "data": {
                        "room_id": room_id,
                        "player_number": player_number,
                        "current_players_count": current_players_count,  # 添加當前玩家數量
                        "players": players_list  # 添加玩家列表
                    }
                }))
                logger.info(f"通知玩家 {room['players'][client]} 有玩家離開")
            except Exception as e:
                logger.error(f"通知玩家離開時出錯: {e}")

        # 如果房間空了，移除房間
        if not room["clients"]:
            del rooms[room_id]
            logger.info(f"房間 {room_id} 已被移除")


async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        logger.info("WebSocket 伺服器已啟動，在 ws://localhost:8765 上運行")
        await asyncio.Future()  # 運行直到手動停止伺服器


if __name__ == "__main__":
    asyncio.run(main())
