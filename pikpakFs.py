import httpx
from pikpakapi import PikPakApi, DownloadStatus
from typing import Dict
from datetime import datetime
import json
import os
import logging
from enum import Enum
import asyncio

class DownloadTaskStatus(Enum):
    pending = "pending"
    downloading = "downloading"
    done = "done"
    error = "error"
    stopped = "stopped"

class PKTaskStatus(Enum):
    pending = "pending"
    remote_downloading = "remote_downloading"
    downloading = "downloading"
    done = "done"
    error = "error"
    stopped = "stopped"

class PkTask:
    _id = 0

    def __init__(self, torrent : str, toDirId : str, status : PKTaskStatus = PKTaskStatus.pending):
        PkTask._id += 1
        self.id = PkTask._id

        self.status = PKTaskStatus.pending
        self.recoverStatus = status
        
        self.runningTask : asyncio.Task = None
        self.name = ""
        self.toDirId = toDirId
        self.nodeId : str = None
        self.torrent = torrent # todo: 将torrent的附加参数去掉再加入
        self.remoteTaskId : str = None

class DownloadTask:
    def __init__(self, nodeId : str, pkTaskId : str, status : DownloadTaskStatus = DownloadTaskStatus.pending):
        self.status = DownloadStatus.pending
        self.recoverStatus = status
        self.pkTaskId = pkTaskId
        self.nodeId = nodeId
        self.runningTask : asyncio.Task = None

class PathWalker():
    def __init__(self, pathStr : str, sep : str = "/"):
        self.__pathSpots : list[str] = []
        if not pathStr.startswith(sep):
            self.__pathSpots.append(".")
        pathSpots = pathStr.split(sep)
        self.__pathSpots.extend(pathSpots)
    
    def IsAbsolute(self) -> bool:
        return len(self.__pathSpots) == 0 or self.__pathSpots[0] != "."

    def AppendSpot(self, spot):
        self.__pathSpots.append(spot)
    
    def Walk(self) -> list[str]:
        return self.__pathSpots

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

class PkToken:
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

class PKFs:
    MAX_PIKPAK_TASKS = 5
    MAX_DOWNLOAD_TASKS = 5

    async def _pktask_pending(self, task : PkTask):
        if task.recoverStatus != PKTaskStatus.pending:
            task.status = task.recoverStatus
            return
        pkTask = await self.client.offline_download(task.torrent, task.toDirId)
        task.remoteTaskId = pkTask["task"]["id"]
        task.nodeId = pkTask["task"]["file_id"]
        task.name = pkTask["task"]["name"]
        task.status = PKTaskStatus.remote_downloading

    async def _pktask_offline_downloading(self, task : PkTask):
        waitTime = 3
        while True:
            await asyncio.sleep(waitTime)
            status = await self.client.get_task_status(task.remoteTaskId, task.nodeId)
            if status in {DownloadStatus.not_found, DownloadStatus.not_downloading, DownloadStatus.error}:
                task.recoverStatus = PKTaskStatus.pending
                task.status = PKTaskStatus.error
                break
            elif status == DownloadStatus.done:
                fileInfo = await self.client.offline_file_info(file_id=task.nodeId)
                node = self.GetNodeById(task.nodeId)
                if node is not None:
                    oldFather = self.GetFatherNode(node)
                    if oldFather is not None:
                        oldFather.childrenId.remove(node.id)
                
                task.toDirId = fileInfo["parent_id"]
                task.name = fileInfo["name"]
                type = fileInfo["kind"]
                if type.endswith("folder"):
                    self.nodes[task.nodeId] = DirNode(task.nodeId, task.name, task.toDirId)    
                else:
                    self.nodes[task.nodeId] = FileNode(task.nodeId, task.name, task.toDirId)
                father = self.GetNodeById(task.toDirId)
                if father.id is not None:
                    father.childrenId.append(task.nodeId)
                task.status = PKTaskStatus.downloading
                break
            waitTime = waitTime * 1.5

    async def _pktask_worker(self, task : PkTask):
        while task.status not in {PKTaskStatus.done, PKTaskStatus.error, PKTaskStatus.stopped}:
            try:
                if task.status == PKTaskStatus.pending:
                    await self._pktask_pending(task)
                elif task.status == PKTaskStatus.remote_downloading:
                    await self._pktask_offline_downloading(task)
                elif task.status == PKTaskStatus.downloading:
                    task.status = PKTaskStatus.done
                else:
                    break
            except asyncio.CancelledError:
                task.recoverStatus = task.status
                task.status = PKTaskStatus.stopped
            except Exception as e:
                logging.error(f"task failed, exception occurred: {e}")
                task.recoverStatus = task.status
                task.status = PKTaskStatus.error
    
    async def _pktask_manager(self):
        while True:
            await asyncio.sleep(1)
            runningTasksNum = 0
            notRunningTasks = [task for task in self.tasks if task.runningTask is None or task.runningTask.done()]
            if len(self.tasks) - len(notRunningTasks) >= PKFs.MAX_PIKPAK_TASKS:
                continue
            for task in [task for task in notRunningTasks if task.status == PKTaskStatus.pending]:
                task.runningTask = asyncio.create_task(self._pktask_worker(task))
                runningTasksNum += 1
                if runningTasksNum >= PKFs.MAX_PIKPAK_TASKS:
                    break
                

    def __init__(self, loginCachePath : str = None, proxy : str = None, rootId = None):
        self.nodes : Dict[str, FsNode] = {} 
        self.root = DirNode(rootId, "", None)
        self.currentLocation = self.root

        self.tasks : list[PkTask] = []
        self.loginCachePath = loginCachePath
        self.proxyConfig = proxy
        self.client : PikPakApi = None
        self._try_login_from_cache()

    def _init_client_by_token(self, token : PkToken):
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
            token = PkToken.from_json(content)
            self._init_client_by_token(token)
            logging.info("successfully load login info from cache") 
    
    def _dump_login_info(self):
        if self.loginCachePath is None:
            return
        with open(self.loginCachePath, 'w', encoding='utf-8') as file:
            token = PkToken(self.client.username, self.client.password, self.client.access_token, self.client.refresh_token, self.client.user_id)
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

    async def StopTask(self, taskId : int):
        pass

    async def ResumeTask(self, taskId : int):
        pass

    async def RetryTask(self, taskId : int):
        task = next((t for t in self.tasks if t.id == taskId), None)
        if task and task.status == PKTaskStatus.error:
            task.status = PKTaskStatus.pending

    def Start(self):
        asyncio.create_task(self._pktask_manager())

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

    async def Download(self, url : str, dirNode : DirNode) -> PkTask :
        task = PkTask(url, dirNode.id)
        self.tasks.append(task)
        return task

    async def QueryTasks(self, filterByStatus : PKTaskStatus = None) -> list[PkTask]:
        if filterByStatus is None:
            return self.tasks
        return [task for task in self.tasks if task.status == filterByStatus]

    async def Delete(self, nodes : list[FsNode]) -> None:
        nodeIds = [node.id for node in nodes]
        await self.client.delete_to_trash(nodeIds)
        for node in nodes:
            father = self.GetFatherNode(node)
            father.childrenId.remove(node.id)