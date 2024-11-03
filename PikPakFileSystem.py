import httpx
from pikpakapi import PikPakApi, DownloadStatus
from typing import Dict
from datetime import datetime
import json
import os
import logging
from typing import Any

class NodeBase:
    def __init__(self, id : str, name : str, fatherId : str):
        self.id = id
        self.name = name
        self._father_id = fatherId
        self.lastUpdate : datetime = None

class DirNode(NodeBase):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.children_id : list[str] = []

class FileNode(NodeBase):
    def __init__(self, id : str, name : str, fatherId : str):
        super().__init__(id, name, fatherId)
        self.url : str = None

class PikPakFileSystem:
    #region 内部接口
    def __init__(self, auth_cache_path : str = None, proxy_address : str = None, root_id : str = None):
        # 初始化虚拟文件节点
        self._nodes : Dict[str, NodeBase] = {} 
        self._root : DirNode = DirNode(root_id, "", None)
        self._cwd : DirNode = self._root

        # 初始化鉴权和代理信息
        self._auth_cache_path : str = auth_cache_path
        self.proxy_address : str = proxy_address
        self._pikpak_client : PikPakApi = None
        self._try_login_from_cache()
        
        
    #region 鉴权信息相关
    class PikPakToken:
        def __init__(self, username : str, password : str, access_token : str, refresh_token : str, user_id : str):
            self.username : str = username
            self.password : str = password
            self.access_token : str = access_token
            self.refresh_token : str = refresh_token
            self.user_id : str = user_id

        def to_json(self):
            return json.dumps(self.__dict__)

        @classmethod
        def from_json(cls, json_str):
            data = json.loads(json_str)
            return cls(**data)

    def _init_client_by_token(self, token : PikPakToken) -> None:
        self._init_client_by_username_and_password(token.username, token.password)
        self._pikpak_client.access_token = token.access_token
        self._pikpak_client.refresh_token = token.refresh_token
        self._pikpak_client.user_id = token.user_id
        self._pikpak_client.encode_token()

    def _init_client_by_username_and_password(self, username : str, password : str) -> None:
        httpx_client_args : Dict[str, Any] = None
        if self.proxy_address != None:
            httpx_client_args = {
                "proxy": self.proxy_address,
                "transport": httpx.AsyncHTTPTransport()
            }

        self._pikpak_client = PikPakApi(
            username = username,
            password = password,
            httpx_client_args=httpx_client_args)

    def _try_login_from_cache(self) -> None:
        if self._auth_cache_path is None:
            return
        if not os.path.exists(self._auth_cache_path):
            return
        try:
            with open(self._auth_cache_path, 'r', encoding='utf-8') as file:
                content : str = file.read()
                token : PikPakFileSystem.PikPakToken = PikPakFileSystem.PikPakToken.from_json(content)
                self._init_client_by_token(token)
                logging.info("successfully load login info from cache") 
        except Exception as e:
            logging.error(f"failed to load login info from cache, exception occurred: {e}")

    def _dump_login_info(self) -> None:
        if self._auth_cache_path is None:
            return
        with open(self._auth_cache_path, 'w', encoding='utf-8') as file:
            token : PikPakFileSystem.PikPakToken = PikPakFileSystem.PikPakToken(self._pikpak_client.username, self._pikpak_client.password, self._pikpak_client.access_token, self._pikpak_client.refresh_token, self._pikpak_client.user_id)
            file.write(token.to_json())
            logging.info("successfully dump login info to cache")

    #endregion

    #region 文件系统相关
    class PathWalker():
        def __init__(self, path : str, sep : str = "/"):
            self._path_spots : list[str] = []
            if not path.startswith(sep):
                self._path_spots.append(".")
            path_spots : list[str] = path.split(sep)
            self._path_spots.extend(path_spots)
        
        def IsAbsolute(self) -> bool:
            return len(self._path_spots) == 0 or self._path_spots[0] != "."

        def AppendSpot(self, spot) -> None:
            self._path_spots.append(spot)
        
        def Walk(self) -> list[str]:
            return self._path_spots

    async def _get_node_by_id(self, id : str) -> NodeBase:
        if id == self._root.id:
            return self._root
        if id not in self._nodes:
            return None
        return self._nodes[id]

    async def _get_father_node(self, node : NodeBase) -> NodeBase:
        if node is self._root:
            return self._root
        return await self._get_node_by_id(node._father_id)

    async def _add_node(self, node : NodeBase) -> None:
        self._nodes[node.id] = node
        father = await self._get_father_node(node)
        if father is not None and isinstance(father, DirNode):
            father.children_id.append(node.id)

    async def _remove_node(self, node : NodeBase) -> None:
        father = await self._get_father_node(node)
        if father is not None and isinstance(father, DirNode):
            father.children_id.remove(node.id)
        self._nodes.pop(node.id)

    async def _find_child_in_dir_by_name(self, dir : DirNode, name : str) -> NodeBase:
        if dir is self._root and name == "":
            return self._root
        for child_id in dir.children_id:
            node = await self._get_node_by_id(child_id)
            if node.name == name:
                return node
        return None

    async def _refresh(self, node : NodeBase):
        if isinstance(node, DirNode):
            if node.lastUpdate != None:
                return
            next_page_token : str = None
            children_info : list[Dict[str, Any]] = []
            while True:
                dir_info : Dict[str, Any] = await self._pikpak_client.file_list(parent_id = node.id, next_page_token=next_page_token)
                next_page_token = dir_info["next_page_token"]
                children_info.extend(dir_info["files"])
                if next_page_token is None or next_page_token == "":
                    break
            
            node.children_id.clear()
            for child_info in children_info:
                id : str = child_info["id"]
                name : str = child_info["name"]
                
                child : NodeBase = await self._get_node_by_id(id)
                if child is None:
                    if child_info["kind"].endswith("folder"):
                        child = DirNode(id, name, node.id)
                    else:
                        child = FileNode(id, name, node.id)
                child.name = name
                child._father_id = node.id
                await self._add_node(child)
        elif isinstance(node, FileNode):
            result = await self._pikpak_client.get_download_url(node.id)
            node.url = result["web_content_link"]
        
        node.lastUpdate = datetime.now()

    async def _path_to_node(self, path : str) -> NodeBase:
        father, son_name = await self._path_to_father_node_and_son_name(path)
        if son_name == "":
            return father
        if isinstance(father, DirNode):
            return await self._find_child_in_dir_by_name(father, son_name)
        return None

    async def _path_to_father_node_and_son_name(self, path : str) -> tuple[NodeBase, str]:
        path_walker : PikPakFileSystem.PathWalker = PikPakFileSystem.PathWalker(path)
        father : NodeBase = None
        son_name : str = None
        current : NodeBase = self._root if path_walker.IsAbsolute() else self._cwd
        
        for spot in path_walker.Walk():
            if current is None:
                father = None
                break
            if spot == "..":
                current = await self._get_father_node(current)
                continue
            father = current
            if not isinstance(current, DirNode):
                current = None
                continue
            await self._refresh(current)
            if spot == ".":
                continue
            sonName = spot
            current = await self._find_child_in_dir_by_name(current, spot)
        
        if current != None:
            father = await self._get_father_node(current)
            sonName = current.name
        
        return father, sonName

    async def _node_to_path(self, node : NodeBase, root : NodeBase = None) -> str:
        if root is None:
            root = self._root
        if node is root:
            return "/"
        spots : list[str] = []
        current = node
        while current is not root:
            spots.append(current.name)
            current = await self._get_father_node(current)
        spots.append("")
        return "/".join(reversed(spots))
    
    async def _is_ancestors_of(self, node_a : NodeBase, node_b : NodeBase) -> bool:
        if node_b is node_a:
            return False
        if node_a is self._root:
            return True
        while node_b._father_id != self._root.id:
            node_b = await self._get_father_node(node_b)
            if node_b is node_a:
                return True
        return False
    #endregion

    #endregion

    #region 对外接口
    async def Login(self, username : str = None, password : str = None) -> None:
        if self._pikpak_client != None and username is None and password is None:
            username = self._pikpak_client.username
            password = self._pikpak_client.password

        if username == None and password == None:
            raise Exception("Username and password are required")
        
        self._init_client_by_username_and_password(username, password)
        await self._pikpak_client.login()
        self._dump_login_info()

    async def IsDir(self, path : str) -> bool:
        node = await self._path_to_node(path)
        return isinstance(node, DirNode)
    
    async def SplitPath(self, path : str) -> tuple[str, str]:
        father, son_name = await self._path_to_father_node_and_son_name(path)
        return await self._node_to_path(father), son_name

    async def GetFileUrl(self, path : str) -> str:
        node = await self._path_to_node(path)
        if not isinstance(node, FileNode):
            return None
        await self._refresh(node)
        return node.url

    async def GetChildrenNames(self, path : str, ignore_files : bool) -> list[str]:
        node = await self._path_to_node(path)
        if not isinstance(node, DirNode):
            return []
        await self._refresh(node)
        children_names : list[str] = []
        for child_id in node.children_id:
            child = await self._get_node_by_id(child_id)
            if ignore_files and isinstance(child, FileNode):
                continue
            children_names.append(child.name)
        return children_names

    async def Delete(self, paths : list[str]) -> None:
        nodes = [await self._path_to_node(path) for path in paths]
        for node in nodes:
            if await self._is_ancestors_of(node, self._cwd):
                raise Exception("Cannot delete ancestors")
        await self._pikpak_client.delete_to_trash([node.id for node in nodes])
        for node in nodes:
            await self._remove_node(node)
    
    async def MakeDir(self, path : str) -> None:
        father_path, son_name = await self._path_to_father_node_and_son_name(path)
        father = await self._path_to_node(father_path)
        result = await self._pikpak_client.create_folder(son_name, father.id)
        id = result["file"]["id"]
        name = result["file"]["name"]
        son = DirNode(id, name, father.id)
        await self._add_node(son)

    async def SetCwd(self, path : str) -> None:
        node = await self._path_to_node(path)
        if not isinstance(node, DirNode):
            raise Exception("Not a directory")
        self._cwd = node

    async def GetCwd(self) -> str:
        return await self._node_to_path(self._cwd)
    
    async def GetChildren(self, node : NodeBase) -> list[NodeBase]:
        if not isinstance(node, DirNode):
            return []
        await self._refresh(node)
        return [await self._get_node_by_id(child_id) for child_id in node.children_id]

    async def PathToNode(self, path : str) -> NodeBase:
        node = await self._path_to_node(path)
        if node is None:
            return None
        return node
    
    async def NodeToPath(self, from_node : NodeBase, to_node : NodeBase) -> str:
        return await self._node_to_path(to_node, from_node)

    async def RemoteDownload(self, torrent : str, remote_base_path : str) -> tuple[str, str]:
        node = await self._path_to_node(remote_base_path)
        info = await self._pikpak_client.offline_download(torrent, node.id)
        return info["task"]["file_id"], info["task"]["id"]

    async def QueryTaskStatus(self, task_id : str, node_id : str) -> DownloadStatus:
        return await self._pikpak_client.get_task_status(task_id, node_id)
    
    async def UpdateNode(self, node_id : str) -> NodeBase:
        node : NodeBase = await self._get_node_by_id(node_id)
        if node is None:
            info = await self._pikpak_client.offline_file_info(node_id)
            kind = info["kind"]
            parent_id = info["parent_id"]
            name = info["name"]
            if kind.endswith("folder"):
                node = DirNode(node_id, name, parent_id)    
            else:
                node = FileNode(node_id, name, parent_id)
            await self._add_node(node)
        node.lastUpdate = None
        return node

    #endregion