import httpx
from hashlib import md5
from pikpakapi import PikPakApi
from typing import Dict
from datetime import datetime
import json
import re
import os
import logging

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

class VirtFsNode:
    def __init__(self, id : str, name : str, fatherId : str):
        self.id = id
        self.name = name
        self.fatherId = fatherId
        self.lastUpdate : datetime = None

class DirNode(VirtFsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.childrenId : list[str] = []
        
class FileNode(VirtFsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.url : str = None

def IsDir(node : VirtFsNode) -> bool:
        return isinstance(node, DirNode)
    
def IsFile(node : VirtFsNode) -> bool:
    return isinstance(node, FileNode)

class PikpakToken:
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

class PKVirtFs:
    def __init__(self, loginCachePath : str = None, proxy : str = None, rootId = None):
        self.nodes : Dict[str, VirtFsNode] = {} 
        self.root = DirNode(rootId, "", None)
        self.currentLocation = self.root

        self.loginCachePath = loginCachePath
        self.proxyConfig = proxy
        self.client : PikPakApi = None
        self._try_login_from_cache()

    def _init_client_by_token(self, token : PikpakToken):
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
            token = PikpakToken.from_json(content)
            self._init_client_by_token(token)
            logging.info("successfully load login info from cache") 
    
    def _dump_login_info(self):
        if self.loginCachePath is None:
            return
        with open(self.loginCachePath, 'w', encoding='utf-8') as file:
            token = PikpakToken(self.client.username, self.client.password, self.client.access_token, self.client.refresh_token, self.client.user_id)
            file.write(token.to_json())
            logging.info("successfully dump login info to cache")
    
    def _is_ancestors_of(self, nodeA : VirtFsNode, nodeB : VirtFsNode) -> bool:
        if nodeB is nodeA:
            return False
        if nodeA is self.root:
            return True
        while nodeB.fatherId != self.root.id:
            nodeB = self.nodes[nodeB.fatherId]
            if nodeB is nodeA:
                return True
        return False

    def GetNodeById(self, id : str) -> VirtFsNode:
        if id == self.root.id:
            return self.root
        return self.nodes[id]

    def GetFatherNode(self, node : VirtFsNode) -> VirtFsNode:
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

    async def Refresh(self, node : VirtFsNode):
        if node.lastUpdate != None:
            return
        if IsDir(node):
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
                child : VirtFsNode = None
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

    async def PathToNode(self, pathStr : str) -> VirtFsNode:
        father, sonName = await self.PathToFatherNodeAndNodeName(pathStr)
        if sonName == "":
            return father
        if not IsDir(father):
            return None
        return self.FindChildInDirByName(father, sonName)
      
    async def PathToFatherNodeAndNodeName(self, pathStr : str) -> tuple[VirtFsNode, str]:
        pathWalker = PathWalker(pathStr)
        father : VirtFsNode = None
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

    def NodeToPath(self, node : VirtFsNode) -> str:
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

    def log_json(self, json_obj):
        logging.debug(json.dumps(json_obj, indent=4))

    async def MakeDir(self, node : DirNode, name : str) -> DirNode:
        result = await self.client.create_folder(name, node.id)
        id = result["file"]["id"]
        name = result["file"]["name"]
        newDir = DirNode(id, name, node.id)
        self.nodes[id] = newDir
        node.childrenId.append(id)
        return newDir

    async def Download(self, url : str, dirNode : DirNode) -> None :
        # 默认创建在当前目录下
        # todo: 完善离线下载task相关
        self.log_json(await self.client.offline_download(url, dirNode.id))

    async def Delete(self, nodes : list[VirtFsNode]) -> None:
        nodeIds = [node.id for node in nodes]
        await self.client.delete_to_trash(nodeIds)
        for node in nodes:
            father = self.GetFatherNode(node)
            father.childrenId.remove(node.id)