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
        pathStr = pathStr.strip()
        if not pathStr.startswith(sep):
            self.__pathSpots.append(".")
        pathSpots = [spot.strip() for spot in pathStr.split(sep) if spot.strip() != ""]
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

class DirNode(VirtFsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.childrenId : list[str] = []
        self.lastUpdate : datetime = None
        
class FileNode(VirtFsNode):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.lastUpdate : datetime = None

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

class VirtFs:
    def __CalcMd5(self, text : str):
        return md5(text.encode()).hexdigest()

    def __ToDir(self, node : VirtFsNode) -> DirNode:
        if isinstance(node, DirNode):
            return node
        return None
    
    def __ToFile(self, node : VirtFsNode) -> FileNode:
        if isinstance(node, FileNode):
            return node
        return None

    def __init__(self, loginCachePath : str = None, proxy : str = None):
        self.nodes : Dict[str, VirtFsNode] = {} 
        self.root = DirNode(None, "", None)
        self.currentLocation = self.root

        self.loginCachePath = loginCachePath
        self.proxyConfig = proxy
        self.client : PikPakApi = None
        self.__TryLoginFromCache()

    def __InitClientByToken(self, token : PikpakToken):
        self.__InitClientByUsernamePassword(token.username, token.password)
        self.client.access_token = token.access_token
        self.client.refresh_token = token.refresh_token
        self.client.user_id = token.user_id
        self.client.encode_token()

    def __InitClientByUsernamePassword(self, username : str, password : str):
        httpx_client_args = None
        if self.proxyConfig != None:
            httpx_client_args = {
                "proxy": self.proxyConfig,
                "transport": httpx.AsyncHTTPTransport(retries=1),
            }

        self.client = PikPakApi(
            username = username,
            password = password,
            httpx_client_args=httpx_client_args)

    def __TryLoginFromCache(self):
        if self.loginCachePath == None:
            return
        if not os.path.exists(self.loginCachePath):
            return
        with open(self.loginCachePath, 'r', encoding='utf-8') as file:
            content = file.read()
            token = PikpakToken.from_json(content)
            self.__InitClientByToken(token)
            logging.info("successfully load login info from cache") 
    
    def __DumpLoginInfo(self):
        if self.loginCachePath == None:
            return
        with open(self.loginCachePath, 'w', encoding='utf-8') as file:
            token = PikpakToken(self.client.username, self.client.password, self.client.access_token, self.client.refresh_token, self.client.user_id)
            file.write(token.to_json())
            logging.info("successfully dump login info to cache")
    
    def __IsAncestorsOf(self, nodeA : VirtFsNode, nodeB : VirtFsNode) -> bool:
        if nodeB is nodeA:
            return False
        if nodeA is self.root:
            return True
        while nodeB.fatherId != None:
            nodeB = self.nodes[nodeB.fatherId]
            if nodeB is nodeA:
                return True
        return False

    async def __RefreshDirectory(self, dirNode : DirNode):
        dirInfo = await self.client.file_list(parent_id = dirNode.id)
        nodes = dirInfo["files"]
        dirNode.childrenId.clear()

        for node in nodes:
            child : VirtFsNode = None
            id = node["id"]
            name = node["name"]
            fatherId = dirNode.id
            if id in self.nodes:
                child = self.nodes[id]
            else:
                child = DirNode(id, name, fatherId) if node["kind"].endswith("folder") else FileNode(id, name, fatherId)
                self.nodes[id] = child
            child.name = name
            child.fatherId = fatherId
            dirNode.childrenId.append(id)
        dirNode.lastUpdate = datetime.now()

    def __FindChildInDirByName(self, dir : DirNode, name : str):
        if dir is self.root and name == "":
            return self.root
        for childId in dir.childrenId:
            node = self.nodes[childId]
            if name == node.name:
                return node
        return None

    async def __PathToNode(self, pathStr : str) -> VirtFsNode:
        father, sonName = await self.__PathToFatherNodeAndNodeName(pathStr)
        fatherDir = self.__ToDir(father)
        if fatherDir == None:
            return None
        return self.__FindChildInDirByName(father, sonName)
    
    async def __PathToFatherNodeAndNodeName(self, pathStr : str) -> tuple[VirtFsNode, str]:
        pathWalker = PathWalker(pathStr)
        father : VirtFsNode = None
        sonName : str = None
        current = self.root if pathWalker.IsAbsolute() else self.currentLocation
        
        for spot in pathWalker.Walk():
            if current == None:
                father = None
                break
            if spot == "..":
                current = self.root if current.fatherId == None else self.nodes[current.fatherId]
                continue
            
            father = current

            currentDir = self.__ToDir(current)
            if currentDir == None:
                current = None
                continue
                
            if currentDir.lastUpdate == None:
                await self.__RefreshDirectory(currentDir)
            
            if spot == ".":
                continue
            
            sonName = spot
            current = self.__FindChildInDirByName(currentDir, spot)
            
        if current != None:
            currentDir = self.__ToDir(current)
            if currentDir != None:
                await self.__RefreshDirectory(currentDir)
            father = self.root if current.fatherId == None else self.nodes[current.fatherId]
            sonName = current.name
        
        return father, sonName

    async def __MakeDir(self, node : DirNode, name : str) -> DirNode:
        await self.client.create_folder(name, node.id)
        await self.__RefreshDirectory(node)
        return self.__ToDir(self.__FindChildInDirByName(node, name))

    async def __NodeToPath(self, node : VirtFsNode) -> str:
        spots : list[str] = [""]
        current = node
        while current.id != None:
            spots.append(current.name)
            if current.fatherId == None:
                break
            current = self.nodes[current.fatherId]
        spots.append("")
        return "/".join(reversed(spots))

    async def login(self, username : str = None, password : str = None) -> str:
        if self.client != None and username == None and password == None:
            username = self.client.username
            password = self.client.password

        if self.client != None and self.client.username == username and self.client.password == password:
            logging.info("already login, try refresh token")
            try:
                await self.client.refresh_access_token()
                self.__DumpLoginInfo()
                return "success"
            except Exception:
                logging.info("Refresh access token failed! Try relogin")
        
        self.__InitClientByUsernamePassword(username, password)
        await self.client.login()
        self.__DumpLoginInfo()
        return "success"

    async def ls(self, pathStr : str = "") -> str:
        dirNode = self.__ToDir(await self.__PathToNode(pathStr))
        if dirNode == None:
            return f"path not found or is file: {pathStr}"      
        result = []
        for childId in dirNode.childrenId:
            node = self.nodes[childId]
            result.append(node.name)
        return "\n".join(result)
    
    async def cd(self, pathStr : str = "") -> str:
        dirNode = self.__ToDir(await self.__PathToNode(pathStr))
        if dirNode == None:
            return f"path not found or is file: {pathStr}"
        self.currentLocation = dirNode
        return "success"

    async def cwd(self) -> str:
        path = await self.__NodeToPath(self.currentLocation)
        return path if path != None else "cwd failed"
    
    async def geturl(self, pathStr : str) -> str:
        fileNode = self.__ToFile(await self.__PathToNode(pathStr))
        if fileNode == None:
            return f"path not found or is not file: {pathStr}"
        
        result = await self.client.get_download_url(fileNode.id)
        return result["web_content_link"]
    
    async def mkdir(self, pathStr : str) -> str:
        father, target = await self.__PathToFatherNodeAndNodeName(pathStr)
        fatherDir = self.__ToDir(father)
        if fatherDir == None:
            return "Failed to locate"
        if self.__FindChildInDirByName(fatherDir, target) != None:
            return f"Path {pathStr} already existed"
        await self.__MakeDir(fatherDir, target)
        return "success"

    async def download(self, url : str, pathStr : str = "") -> str :
        # todo: 完善离线下载task相关
        dirNode = self.__ToDir(await self.__PathToNode(pathStr))
        if dirNode == None:
            return f"path not found or is file: {pathStr}"

        subFolderName = self.__CalcMd5(url)
        newDirNode = await self.__MakeDir(dirNode, subFolderName)
        if newDirNode == None:
            return f"falied to create sub folder {subFolderName}"

        await self.client.offline_download(url, newDirNode.id)
        return subFolderName

    async def update(self, pathStr : str = ""):
        dirNode = self.__ToDir(await self.__PathToNode(pathStr))
        if dirNode == None:
            return f"path not found or is file: {pathStr}"
        await self.__RefreshDirectory(dirNode)
        return "success"

    async def delete(self, pathStr : str):
        father, name = await self.__PathToFatherNodeAndNodeName(pathStr)
        fatherDir = self.__ToDir(father)
        if fatherDir == None:
            return "Failed to locate"
        node = self.__FindChildInDirByName(fatherDir, name)
        if node == None:
            return f"path {pathStr} not existed"
        
        if self.currentLocation is node or self.__IsAncestorsOf(node, self.currentLocation):
            return f"delete self or ancestor is not allowed"
        await self.client.delete_to_trash([node.id])
        await self.__RefreshDirectory(fatherDir)
        return "success"

    async def HandlerCommand(self, command):
        result = re.findall(r'"(.*?)"|(\S+)', command)
        filtered_result = [item for sublist in result for item in sublist if item]

        cmd = filtered_result[0]
        args = filtered_result[1:]

        method = getattr(self, cmd)
        if method == None:
            return f"Unknown command: {cmd}"
        output = await method(*args)
        logging.info(f"{command} : {repr(output)}")
        return output