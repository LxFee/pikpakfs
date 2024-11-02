在Pikpak Api基础上套了一层文件系统，更好自动化离线下载

运行: python main.py 

Todo:

- [x] 实现自定义根路径
- [x] 异步输出频率过高会导致卡死，似乎会多创建一个线程
- [x] 实现Task队列管理
- [x] 自动刷新文件系统缓存
- [x] 分析以下方法的返回值：offline_file_info、offline_list
- [ ] 实现本地下载队列（多文件，文件夹）
- [ ] 实现任务暂停、继续、恢复
- [ ] 持久化数据
- [ ] 添加测试用例
- [ ] 完全类型化


### 协议结构
1. offline_download
```json
{
    "upload_type": "UPLOAD_TYPE_URL",
    "url": {
        "kind": "upload#url"
    },
    "task": {
        "kind": "drive#task",
        "id": "VOAMocJorcA09bRr-3bEDUbYo1",
        "name": "[FLsnow][Genshiken-2daime][BDRip]",
        "type": "offline",
        "user_id": "ZEBRT8Wc1IzU1rfZ",
        "statuses": [],
        "status_size": 56,
        "params": {
            "predict_speed": "73300775185",
            "predict_type": "3"
        },
        "file_id": "VOAMocKArcA09bRr-3bEDUbZo1",
        "file_name": "[FLsnow][Genshiken-2daime][BDRip]",
        "file_size": "29071069771",
        "message": "Saving",
        "created_time": "2024-10-29T18:29:11.092+08:00",
        "updated_time": "2024-10-29T18:29:11.092+08:00",
        "third_task_id": "",
        "phase": "PHASE_TYPE_RUNNING",
        "progress": 0,
        "icon_link": "",
        "callback": "",
        "space": ""
    }
}
```

2. offline_file_info
```json
{
    "kind": "drive#folder",
    "id": "VOAMocKArcA09bRr-3bEDUbZo1",
    "parent_id": "VNTQEPvYTRlbqP1pB2YGZorwo1",
    "name": "[FLsnow][Genshiken-2daime][BDRip](1)",
    "user_id": "ZEBRT8Wc1IzU1rfZ",
    "size": "0",
    "revision": "0",
    "file_extension": "",
    "mime_type": "",
    "starred": false,
    "web_content_link": "",
    "created_time": "2024-10-29T18:29:13.251+08:00",
    "modified_time": "2024-10-29T18:29:13.251+08:00",
    "icon_link": "https://static.mypikpak.com/7d6933d5cde34f200366685cba0cbc4592cfd363",
    "thumbnail_link": "https://sg-thumbnail-drive.mypikpak.com/v0/screenshot-thumbnails/788AB60820B162FD988606CE988FBC40B8C6EA8D/720/2048",
    "md5_checksum": "",
    "hash": "",
    "links": {},
    "phase": "PHASE_TYPE_COMPLETE",
    "audit": {
        "status": "STATUS_OK",
        "message": "Normal resource",
        "title": ""
    },
    "medias": [],
    "trashed": false,
    "delete_time": "",
    "original_url": "",
    "params": {
        "platform_icon": "https://static.mypikpak.com/21ecdc2c6b2372cdee91b193df9a6248b885a1b0",
        "small_thumbnail": "https://sg-thumbnail-drive.mypikpak.com/v0/screenshot-thumbnails/788AB60820B162FD988606CE988FBC40B8C6EA8D/240/720",
        "url": "magnet:?xt=urn:btih:7c0e7e3e3828c22b49e903beefcee69ec2a4986e"
    },
    "original_file_index": 0,
    "space": "",
    "apps": [],
    "writable": true,
    "folder_type": "NORMAL",
    "sort_name": "",
    "user_modified_time": "2024-10-29T18:29:13.251+08:00",
    "spell_name": [],
    "file_category": "OTHER",
    "tags": [],
    "reference_events": []
}
```

3. offline_list
```json

{
    "tasks": [
        {
            "kind": "drive#task",
            "id": "VOASrVEVIQmaCBjEu8Y1VDb7o1",
            "name": "[LoliHouse] Mahoutsukai ni Narenakatta Onnanoko no Hanashi - 04 [WebRip 1080p HEVC-10bit AAC SRTx2].mkv",
            "type": "offline",
            "user_id": "ZEBRT8Wc1IzU1rfZ",
            "statuses": [],
            "status_size": 1,
            "params": {
                "age": "0",
                "mime_type": "video/x-matroska",
                "predict_speed": "73300775185",
                "predict_type": "3",
                "url": "magnet:?xt=urn:btih:02816d3bd51f9e3ac72c986cc65f3f7a2b201b5b"
            },
            "file_id": "VOASrVFTIQmaCBjEu8Y1VDbAo1",
            "file_name": "[LoliHouse] Mahoutsukai ni Narenakatta Onnanoko no Hanashi - 04 [WebRip 1080p HEVC-10bit AAC SRTx2].mkv",
            "file_size": "726857457",
            "message": "Saving",
            "created_time": "2024-10-30T22:39:27.712+08:00",
            "updated_time": "2024-10-30T22:39:27.712+08:00",
            "third_task_id": "",
            "phase": "PHASE_TYPE_RUNNING",
            "progress": 90,
            "icon_link": "https://static.mypikpak.com/39998a187e280e2ee9ceb5f58315a1bcc744fa64",
            "callback": "",
            "reference_resource": {
                "@type": "type.googleapis.com/drive.ReferenceFile",
                "kind": "drive#file",
                "id": "VOASrVFTIQmaCBjEu8Y1VDbAo1",
                "parent_id": "VNTQEPvYTRlbqP1pB2YGZorwo1",
                "name": "[LoliHouse] Mahoutsukai ni Narenakatta Onnanoko no Hanashi - 04 [WebRip 1080p HEVC-10bit AAC SRTx2].mkv",
                "size": "726857457",
                "mime_type": "video/x-matroska",
                "icon_link": "https://static.mypikpak.com/39998a187e280e2ee9ceb5f58315a1bcc744fa64",
                "hash": "",
                "phase": "PHASE_TYPE_RUNNING",
                "thumbnail_link": "",
                "params": {},
                "space": "",
                "medias": [],
                "starred": false,
                "tags": []
            },
            "space": ""
        }
    ],
    "next_page_token": "",
    "expires_in": 3
}

{
    "tasks": [],
    "next_page_token": "",
    "expires_in": 10
}
```