import httpx
from pikpakapi import PikPakApi, DownloadStatus
from typing import Dict
from datetime import datetime
import json
import os
import logging
from enum import Enum
import asyncio
import uuid
from utils import PathWalker
from typing import Callable, Awaitable

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PAUSED = "paused"

class PikPakTaskStatus(Enum):
    PENDING = "pending"
    REMOTE_DOWNLOADING = "remote downloading"
    LOCAL_DOWNLOADING = "local downloading"

class FileDownloadTaskStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"

class UnRecoverableError(Exception):
    def __init__(self, message):
        super().__init__(message)

class TaskBase:
    def __init__(self, id : str, tag : str = "", maxConcurrentNumber = -1):
        self.id : str = uuid.uuid4() if id is None else id 
        self.tag : str = tag
        self.maxConcurrentNumber : int = maxConcurrentNumber

        self._status : TaskStatus = TaskStatus.PENDING
        
        self.worker : asyncio.Task = None
        self.handler : Callable[..., Awaitable] = None

class PikPakTask(TaskBase):
    TAG = "PikPakTask"
    MAX_CONCURRENT_NUMBER = 5

    def __init__(self, torrent : str, toDirId : str, id : str = None, status : PikPakTaskStatus = PikPakTaskStatus.PENDING):
        super().__init__(id, PikPakTask.TAG, PikPakTask.MAX_CONCURRENT_NUMBER)
        self.status : PikPakTaskStatus = status
        self.toDirId : str = toDirId
        self.nodeId : str = None
        self.name : str = ""
        self.torrent : str = torrent # todo: 将torrent的附加参数去掉再加入
        self.remoteTaskId : str = None

class FileDownloadTask(TaskBase):
    TAG = "FileDownloadTask"
    MAX_CONCURRENT_NUMBER = 5

    def __init__(self, nodeId : str, PikPakTaskId : str, id : str = None, status : FileDownloadTaskStatus = FileDownloadTaskStatus.PENDING):
        super().__init__(id, FileDownloadTask.TAG, FileDownloadTask.MAX_CONCURRENT_NUMBER)
        self.status : FileDownloadTaskStatus = status
        self.PikPakTaskId : str = PikPakTaskId
        self.nodeId : str = nodeId

async def TaskWorker(task : TaskBase):
    try:
        if task._status != TaskStatus.PENDING:
            return
        task._status = TaskStatus.RUNNING
        await task.handler(task)
        task._status = TaskStatus.DONE
    except asyncio.CancelledError:
        task._status = TaskStatus.PAUSED
    except Exception as e:
        logging.error(f"task failed, exception occurred: {e}")
        task._status = TaskStatus.ERROR

async def TaskManager(taskQueues : Dict[str, list[TaskBase]]):
    # todo: 处理取消的情况
    while True:
        await asyncio.sleep(1)
        for taskQueue in taskQueues.values():
            notRunningTasks = [task for task in taskQueue if task.worker is None or task.worker.done()]
            runningTasksNumber = len(taskQueue) - len(notRunningTasks)
            for task in [task for task in notRunningTasks if task._status == TaskStatus.PENDING]:
                if runningTasksNumber >= task.maxConcurrentNumber:
                    break
                task.worker = asyncio.create_task(TaskWorker(task))
                runningTasksNumber += 1


class FsNode:
    def __init__(self, id : str, name : str, fatherId : str):
        self.id = id
        self.name = name
        self.fatherId = fatherId
        self.lastUpdate : datetime = None

class DirNode(FsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.childrenId : list[str] = []
        
class FileNode(FsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.url : str = None

def IsDir(node : FsNode) -> bool:
        return isinstance(node, DirNode)
    
def IsFile(node : FsNode) -> bool:
    return isinstance(node, FileNode)

class PikPakToken:
    def __init__(self, username, password, access_token, refresh_token, user_id):
        self.username = username
        self.password = password
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.user_id = user_id

    def to_json(self):
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, json_str):
        data = json.loads(json_str)
        return cls(**data)

class PikPakFs:

    async def _pikpak_task_pending(self, task : PikPakTask):
        pikPakTaskInfo = await self.client.offline_download(task.torrent, task.toDirId)
        task.remoteTaskId = pikPakTaskInfo["task"]["id"]
        task.nodeId = pikPakTaskInfo["task"]["file_id"]
        task.status = PikPakTaskStatus.REMOTE_DOWNLOADING

    async def _pikpak_offline_downloading(self, task : PikPakTask):
        waitTime = 3
        while True:
            await asyncio.sleep(waitTime)
            status = await self.client.get_task_status(task.remoteTaskId, task.nodeId)
            if status in {DownloadStatus.not_found, DownloadStatus.not_downloading, DownloadStatus.error}:
                self.status = PikPakTaskStatus.PENDING
                raise Exception(f"remote download failed, status: {status}")
            elif status == DownloadStatus.done:
                break
            waitTime = waitTime * 1.5
        
        fileInfo = await self.client.offline_file_info(file_id=task.nodeId)
        task.toDirId = fileInfo["parent_id"]
        task.name = fileInfo["name"]
        if fileInfo["kind"].endswith("folder"):
            self.nodes[task.nodeId] = DirNode(task.nodeId, task.name, task.toDirId)    
        else:
            self.nodes[task.nodeId] = FileNode(task.nodeId, task.name, task.toDirId)
        father = self.GetNodeById(task.toDirId)
        if father.id is not None and task.nodeId not in father.childrenId:
            father.childrenId.append(task.nodeId)
        task.status = PikPakTaskStatus.LOCAL_DOWNLOADING

    async def _pikpak_task_handler(self, task : PikPakTask):
        while True:
            if task.status == PikPakTaskStatus.PENDING:
                await self._pikpak_task_pending(task)
            elif task.status == PikPakTaskStatus.REMOTE_DOWNLOADING:
                await self._pikpak_offline_downloading(task)
            elif task.status == PikPakTaskStatus.LOCAL_DOWNLOADING:
                break
            else:
                break

    async def _file_download_task_handler(self, task : FileDownloadTask):
        pass

    def _add_task(self, task : TaskBase):
        if self.taskQueues.get(task.tag) is None:
            self.taskQueues[task.tag] = []
        self.taskQueues[task.tag].append(task)
    
    async def StopTask(self, task : TaskBase):
        pass

    async def ResumeTask(self, task : TaskBase):
        pass

    async def RetryTask(self, taskId : str):
        if PikPakTask.TAG not in self.taskQueues:
            return
        for task in self.taskQueues[PikPakTask.TAG]:
            if task.id == taskId and task._status == TaskStatus.ERROR:
                task._status = TaskStatus.PENDING
                break
            elif task.id == taskId:
                break

    def Start(self):
        return asyncio.create_task(TaskManager(self.taskQueues))

    def __init__(self, loginCachePath : str = None, proxy : str = None, rootId = None):
        self.nodes : Dict[str, FsNode] = {} 
        self.root = DirNode(rootId, "", None)
        self.currentLocation = self.root

        self.taskQueues : Dict[str, list[TaskBase]] = {}
        self.loginCachePath = loginCachePath
        self.proxyConfig = proxy
        self.client : PikPakApi = None
        self._try_login_from_cache()

    def _init_client_by_token(self, token : PikPakToken):
        self._init_client_by_username_and_password(token.username, token.password)
        self.client.access_token = token.access_token
        self.client.refresh_token = token.refresh_token
        self.client.user_id = token.user_id
        self.client.encode_token()

    def _init_client_by_username_and_password(self, username : str, password : str):
        httpx_client_args = None
        if self.proxyConfig != None:
            httpx_client_args = {
                "proxy": self.proxyConfig,
                "transport": httpx.AsyncHTTPTransport()
            }

        self.client = PikPakApi(
            username = username,
            password = password,
            httpx_client_args=httpx_client_args)

    def _try_login_from_cache(self):
        if self.loginCachePath is None:
            return
        if not os.path.exists(self.loginCachePath):
            return
        with open(self.loginCachePath, 'r', encoding='utf-8') as file:
            content = file.read()
            token = PikPakToken.from_json(content)
            self._init_client_by_token(token)
            logging.info("successfully load login info from cache") 
    
    def _dump_login_info(self):
        if self.loginCachePath is None:
            return
        with open(self.loginCachePath, 'w', encoding='utf-8') as file:
            token = PikPakToken(self.client.username, self.client.password, self.client.access_token, self.client.refresh_token, self.client.user_id)
            file.write(token.to_json())
            logging.info("successfully dump login info to cache")
    
    def _is_ancestors_of(self, nodeA : FsNode, nodeB : FsNode) -> bool:
        if nodeB is nodeA:
            return False
        if nodeA is self.root:
            return True
        while nodeB.fatherId != self.root.id:
            nodeB = self.nodes[nodeB.fatherId]
            if nodeB is nodeA:
                return True
        return False

    def GetNodeById(self, id : str) -> FsNode:
        if id == self.root.id:
            return self.root
        if id not in self.nodes:
            return None
        return self.nodes[id]

    def GetFatherNode(self, node : FsNode) -> FsNode:
        if node is self.root:
            return self.root
        return self.GetNodeById(node.fatherId)

    def FindChildInDirByName(self, dir : DirNode, name : str):
        if dir is self.root and name == "":
            return self.root
        for childId in dir.childrenId:
            node = self.nodes[childId]
            if name == node.name:
                return node
        return None

    async def Refresh(self, node : FsNode):
        if IsDir(node):
            if node.lastUpdate != None:
                return
            next_page_token = None
            childrenInfo = []
            while True:
                dirInfo = await self.client.file_list(parent_id = node.id, next_page_token=next_page_token)
                next_page_token = dirInfo["next_page_token"]
                currentPageNodes = dirInfo["files"]
                childrenInfo.extend(currentPageNodes)
                if next_page_token is None or next_page_token == "":
                    break
            node.childrenId.clear()

            for childInfo in childrenInfo:
                child : FsNode = None
                id = childInfo["id"]
                name = childInfo["name"]
                fatherId = node.id
                if id in self.nodes:
                    child = self.nodes[id]
                else:
                    child = DirNode(id, name, fatherId) if childInfo["kind"].endswith("folder") else FileNode(id, name, fatherId)
                    self.nodes[id] = child
                child.name = name
                child.fatherId = fatherId
                node.childrenId.append(id)
        elif IsFile(node):
            result = await self.client.get_download_url(node.id)
            node.url = result["web_content_link"]
        
        node.lastUpdate = datetime.now()

    async def PathToNode(self, pathStr : str) -> FsNode:
        father, sonName = await self.PathToFatherNodeAndNodeName(pathStr)
        if sonName == "":
            return father
        if not IsDir(father):
            return None
        return self.FindChildInDirByName(father, sonName)
      
    async def PathToFatherNodeAndNodeName(self, pathStr : str) -> tuple[FsNode, str]:
        pathWalker = PathWalker(pathStr)
        father : FsNode = None
        sonName : str = None
        current = self.root if pathWalker.IsAbsolute() else self.currentLocation
        
        for spot in pathWalker.Walk():
            if current is None:
                father = None
                break
            if spot == "..":
                current = self.GetFatherNode(current)
                continue
            father = current
            if not IsDir(current):
                current = None
                continue
            await self.Refresh(current)
            if spot == ".":
                continue
            sonName = spot
            current = self.FindChildInDirByName(current, spot)
            
        if current != None:
            father = self.GetFatherNode(current)
            sonName = current.name
        
        return father, sonName

    def NodeToPath(self, node : FsNode) -> str:
        if node is self.root:
            return "/"
        spots : list[str] = []
        current = node
        while current is not self.root:
            spots.append(current.name)
            current = self.GetFatherNode(current)
        spots.append("")
        return "/".join(reversed(spots))

    # commands #
    async def Login(self, username : str = None, password : str = None) -> None:
        if self.client != None and username is None and password is None:
            username = self.client.username
            password = self.client.password

        if username == None and password == None:
            raise Exception("Username and password are required")
        
        self._init_client_by_username_and_password(username, password)
        await self.client.login()
        self._dump_login_info()

    async def MakeDir(self, node : DirNode, name : str) -> DirNode:
        result = await self.client.create_folder(name, node.id)
        id = result["file"]["id"]
        name = result["file"]["name"]
        newDir = DirNode(id, name, node.id)
        self.nodes[id] = newDir
        node.childrenId.append(id)
        return newDir

    async def Download(self, url : str, dirNode : DirNode) -> PikPakTask :
        task = PikPakTask(url, dirNode.id)
        task.handler = self._pikpak_task_handler
        self._add_task(task)
        return task

    async def QueryPikPakTasks(self, filterStatus : TaskStatus = None) -> list[PikPakTask]:
        if PikPakTask.TAG not in self.taskQueues:
            return []
        taskQueue = self.taskQueues[PikPakTask.TAG]
        if filterStatus is None:
            return taskQueue
        return [task for task in taskQueue if task._status == filterStatus]

    async def Delete(self, nodes : list[FsNode]) -> None:
        nodeIds = [node.id for node in nodes]
        await self.client.delete_to_trash(nodeIds)
        for node in nodes:
            father = self.GetFatherNode(node)
            father.childrenId.remove(node.id)