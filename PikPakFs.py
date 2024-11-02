import httpx
from pikpakapi import PikPakApi, DownloadStatus
from typing import Dict
from datetime import datetime
import json
import os
import logging
from enum import Enum
import asyncio
import shortuuid
from utils import PathWalker
from typing import Callable, Awaitable
import random

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PAUSED = "paused"

class PikPakTaskStatus(Enum):
    PENDING = "pending"
    REMOTE_DOWNLOADING = "remote"
    LOCAL_DOWNLOADING = "local"
    DONE = "done"

class FileDownloadTaskStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DONE = "done"
    
class UnRecoverableError(Exception):
    def __init__(self, message):
        super().__init__(message)

class TaskBase:
    def __init__(self, id : str, tag : str = "", maxConcurrentNumber = -1):
        self.id : str = shortuuid.uuid() if id is None else id 
        self.tag : str = tag
        self.maxConcurrentNumber : int = maxConcurrentNumber
        self.name : str = ""

        self._status : TaskStatus = TaskStatus.PENDING
        
        self.worker : asyncio.Task = None
        self.handler : Callable[..., Awaitable] = None

class PikPakTask(TaskBase):
    TAG = "PikPakTask"
    MAX_CONCURRENT_NUMBER = 5

    def __init__(self, torrent : str, toDirId : str, nodeId : str = None, status : PikPakTaskStatus = PikPakTaskStatus.PENDING, id : str = None):
        super().__init__(id, PikPakTask.TAG, PikPakTask.MAX_CONCURRENT_NUMBER)
        self.status : PikPakTaskStatus = status
        self.toDirId : str = toDirId
        self.nodeId : str = nodeId
        self.torrent : str = torrent # todo: 将torrent的附加参数去掉再加入
        self.remoteTaskId : str = None

        self.progress : str = ""

    def __eq__(self, other):
        if isinstance(other, PikPakTask):
            return self is other or self.nodeId == other.nodeId
        return False

class FileDownloadTask(TaskBase):
    TAG = "FileDownloadTask"
    MAX_CONCURRENT_NUMBER = 5

    def __init__(self, nodeId : str, PikPakTaskId : str, relativePath : str, status : FileDownloadTaskStatus = FileDownloadTaskStatus.PENDING, id : str = None):
        super().__init__(id, FileDownloadTask.TAG, FileDownloadTask.MAX_CONCURRENT_NUMBER)
        self.status : FileDownloadTaskStatus = status
        self.PikPakTaskId : str = PikPakTaskId
        self.nodeId : str = nodeId
        self.relativePath : str = relativePath
    
    def __eq__(self, other):
        if isinstance(other, FileDownloadTask):
            return self is other or (self.nodeId == other.nodeId and self.PikPakTaskId == other.PikPakTaskId)
        return False

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
        await asyncio.sleep(0.5)
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

    async def _pikpak_local_downloading(self, task : PikPakTask):
        node = self.GetNodeById(task.nodeId)
        if IsFile(node):
            fileDownloadTask = FileDownloadTask(task.nodeId, task.id, self.NodeToPath(node, node))
            fileDownloadTask.handler = self._file_download_task_handler
            fileDownloadTask.name = task.name
            self._add_task(fileDownloadTask)
        elif IsDir(node):
            # 使用广度优先遍历
            queue : list[DirNode] = [node]
            while len(queue) > 0:
                current = queue.pop(0)
                await self.Refresh(current)
                for childId in current.childrenId:
                    child = self.GetNodeById(childId)
                    if IsDir(child):
                        queue.append(child)
                    elif IsFile(child):
                        fileDownloadTask = FileDownloadTask(childId, task.id, self.NodeToPath(child, node))
                        fileDownloadTask.handler = self._file_download_task_handler
                        fileDownloadTask.name = task.name
                        self._add_task(fileDownloadTask)
        
        # 开始等待下载任务完成
        while True:
            fileDownloadTasks = self.taskQueues.get(FileDownloadTask.TAG)
            myTasks = [myTask for myTask in fileDownloadTasks if myTask.PikPakTaskId == task.id]
            allNumber = len(myTasks)
            notCompletedNumber = 0
            pausedNumber = 0
            errorNumber = 0
            for myTask in myTasks:
                if myTask._status == TaskStatus.PAUSED:
                    pausedNumber += 1
                if myTask._status == TaskStatus.ERROR:
                    errorNumber += 1
                if myTask._status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                    notCompletedNumber += 1
            
            runningNumber = allNumber - notCompletedNumber - pausedNumber - errorNumber
            task.progress = f"{runningNumber}/{allNumber} ({pausedNumber}|{errorNumber})"
            
            if notCompletedNumber > 0:
                await asyncio.sleep(0.5)
                continue
            if errorNumber > 0:
                raise Exception("file download failed")
            if pausedNumber > 0:
                raise asyncio.CancelledError()
            break
            
        task.status = PikPakTaskStatus.DONE
            

    async def _pikpak_task_handler(self, task : PikPakTask):
        while True:
            if task.status == PikPakTaskStatus.PENDING:
                await self._pikpak_task_pending(task)
            elif task.status == PikPakTaskStatus.REMOTE_DOWNLOADING:
                await self._pikpak_offline_downloading(task)
            elif task.status == PikPakTaskStatus.LOCAL_DOWNLOADING:
                await self._pikpak_local_downloading(task)
            else:
                break

    async def _file_download_task_handler(self, task : FileDownloadTask):
        if random.randint(1, 5) == 2:
            raise asyncio.CancelledError()
        pass

    def _add_task(self, task : TaskBase):
        if self.taskQueues.get(task.tag) is None:
            self.taskQueues[task.tag] = []
        taskQueue = self.taskQueues[task.tag]
        for other in taskQueue:
            if other == task:
                if other._status != TaskStatus.DONE:
                    other._status = TaskStatus.PENDING
                return
        taskQueue.append(task)
    
    async def GetTaskById(self, taskId : str) -> TaskBase:
        for taskQueue in self.taskQueues.values():
            for task in taskQueue:
                if task.id == taskId:
                    return task
        return None

    async def StopTask(self, taskId : str):
        task = await self.GetTaskById(taskId)
        if task is not None and task._status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            if task.worker is not None:
                task.worker.cancel()
            task.worker = None

    async def ResumeTask(self, taskId : str):
        task = await self.GetTaskById(taskId)
        if task is not None and task._status in {TaskStatus.PAUSED, TaskStatus.ERROR}:
            task._status = TaskStatus.PENDING

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

    async def PathToNode(self, path : str) -> FsNode:
        father, sonName = await self.PathToFatherNodeAndNodeName(path)
        if sonName == "":
            return father
        if not IsDir(father):
            return None
        return self.FindChildInDirByName(father, sonName)
      
    async def PathToFatherNodeAndNodeName(self, path : str) -> tuple[FsNode, str]:
        pathWalker = PathWalker(path)
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

    def NodeToPath(self, node : FsNode, root : FsNode = None) -> str:
        if root is None:
            root = self.root
        if node is root:
            return "/"
        spots : list[str] = []
        current = node
        while current is not root:
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
    
    async def Pull(self, node : FsNode) -> PikPakTask:
        task = PikPakTask("", node.fatherId, node.id, PikPakTaskStatus.LOCAL_DOWNLOADING)
        task.name = node.name
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
    
    async def QueryFileDownloadTasks(self, filterStatus : TaskStatus = None) -> list[FileDownloadTask]:
        if FileDownloadTask.TAG not in self.taskQueues:
            return []
        taskQueue = self.taskQueues[FileDownloadTask.TAG]
        if filterStatus is None:
            return taskQueue
        return [task for task in taskQueue if task._status == filterStatus]

    async def Delete(self, nodes : list[FsNode]) -> None:
        nodeIds = [node.id for node in nodes]
        await self.client.delete_to_trash(nodeIds)
        for node in nodes:
            father = self.GetFatherNode(node)
            father.childrenId.remove(node.id)