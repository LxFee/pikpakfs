import asyncio, nest_asyncio
import cmd2
from functools import wraps
import logging
import threading
import colorlog
from pikpakFs import PKFs, IsDir, IsFile, PKTaskStatus
import os

def setup_logging():
    formatter = colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt='%Y-%m-%d %H:%M:%S',
        reset=True,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        }
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)

    file_handler = logging.FileHandler('app.log')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    logger = logging.getLogger()
    logger.addHandler(handler)
    logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)

setup_logging()
MainLoop : asyncio.AbstractEventLoop = None
Client = PKFs("token.json", proxy="http://127.0.0.1:7890")

def RunSync(func):
    @wraps(func)
    def decorator(*args, **kwargs):
        currentLoop = None
        try:
            currentLoop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if currentLoop is MainLoop:
            return MainLoop.run_until_complete(func(*args, **kwargs))
        else:
            return asyncio.run_coroutine_threadsafe(func(*args, **kwargs), MainLoop).result()
    return decorator

class Console(cmd2.Cmd):
    def _io_worker(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    async def AsyncInput(self, prompt):
        async def _input(prompt):
            return self._read_command_line(prompt)
        future = asyncio.run_coroutine_threadsafe(_input(prompt), self.inputLoop)
        return await asyncio.wrap_future(future)

    async def AsyncPrint(self, *args, **kwargs):
        async def _print(*args, **kwargs):
            print(*args, **kwargs)
        future = asyncio.run_coroutine_threadsafe(_print(*args, **kwargs), self.outputLoop)
        await asyncio.wrap_future(future)

    def __init__(self):
        super().__init__()

    def preloop(self):
        # 1. 设置忽略SIGINT
        import signal
        def signal_handler(sig, frame):
            pass
        signal.signal(signal.SIGINT, signal_handler)

        # 2. 创建IO线程处理输入输出
        self.inputLoop = asyncio.new_event_loop()
        self.inputThread = threading.Thread(target=self._io_worker, args=(self.inputLoop,))
        self.inputThread.start()

        self.outputLoop = asyncio.new_event_loop()
        self.outputThread = threading.Thread(target=self._io_worker, args=(self.outputLoop,))
        self.outputThread.start()

        # 3. 设置console
        self.saved_readline_settings = None
        with self.sigint_protection:
            self.saved_readline_settings = self._set_up_cmd2_readline()
    
    def postloop(self):
        # 1. 还原console设置
        with self.sigint_protection:
            if self.saved_readline_settings is not None:
                self._restore_readline(self.saved_readline_settings)
        
        # 2. 停止IO线程
        # https://stackoverflow.com/questions/51642267/asyncio-how-do-you-use-run-forever
        self.inputLoop.call_soon_threadsafe(self.inputLoop.stop)
        self.inputThread.join()

        self.outputLoop.call_soon_threadsafe(self.outputLoop.stop)
        self.outputThread.join()
    
    # commands #
    def do_debug(self, args):
        """
        Enable debug mode
        """
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Debug mode enabled")

    def do_debugoff(self, args):
        """
        Disable debug mode
        """
        logging.getLogger().setLevel(logging.INFO)
        logging.info("Debug mode disabled")

    login_parser = cmd2.Cmd2ArgumentParser()
    login_parser.add_argument("username", help="username", nargs="?")
    login_parser.add_argument("password", help="password", nargs="?")
    @RunSync
    @cmd2.with_argparser(login_parser)
    async def do_login(self, args):
        """
        Login to pikpak
        """
        await Client.Login(args.username, args.password)
        await self.AsyncPrint("Logged in successfully")

    async def _path_completer(self, text, line, begidx, endidx, filterfiles):   
        father, sonName = await Client.PathToFatherNodeAndNodeName(text)
        if not IsDir(father):
            return []
        matches = []
        matchesNode = []
        for childId in father.childrenId:
            child = Client.GetNodeById(childId)
            if filterfiles and IsFile(child):
                continue
            if child.name.startswith(sonName):
                self.display_matches.append(child.name)
                if sonName == "":
                    matches.append(text + child.name)
                elif text.endswith(sonName):
                    matches.append(text[:text.rfind(sonName)] + child.name)
                matchesNode.append(child)
        if len(matchesNode) == 1 and IsDir(matchesNode[0]):
            matches[0] += "/"
            self.allow_appended_space = False
            self.allow_closing_quote = False
        return matches

    @RunSync
    async def complete_ls(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, False)

    ls_parser = cmd2.Cmd2ArgumentParser()
    ls_parser.add_argument("path", help="path", default="", nargs="?", type=RunSync(Client.PathToNode))
    @RunSync
    @cmd2.with_argparser(ls_parser)
    async def do_ls(self, args):
        """
        List files in a directory
        """
        node = args.path
        if node is None:
            await self.AsyncPrint("Invalid path")
            return
        await Client.Refresh(node)
        if IsDir(node):
            for childId in node.childrenId:
                child = Client.GetNodeById(childId)
                await self.AsyncPrint(child.name)
        elif IsFile(node):
            await self.AsyncPrint(f"{node.name}: {node.url}")            
    
    @RunSync
    async def complete_cd(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, True)

    cd_parser = cmd2.Cmd2ArgumentParser()
    cd_parser.add_argument("path", help="path", default="", nargs="?", type=RunSync(Client.PathToNode))
    @RunSync
    @cmd2.with_argparser(cd_parser)
    async def do_cd(self, args):
        """
        Change directory
        """
        node = args.path
        if not IsDir(node):
            await self.AsyncPrint("Invalid directory")
            return
        Client.currentLocation = node
    
    @RunSync
    async def do_cwd(self, args):
        """
        Print current working directory
        """
        await self.AsyncPrint(Client.NodeToPath(Client.currentLocation))

    def do_clear(self, args):
        """
        Clear the terminal screen
        """
        os.system('cls' if os.name == 'nt' else 'clear')

    @RunSync
    async def complete_rm(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, False)

    rm_parser = cmd2.Cmd2ArgumentParser()
    rm_parser.add_argument("paths", help="paths", default="", nargs="+", type=RunSync(Client.PathToNode))
    @RunSync
    @cmd2.with_argparser(rm_parser)
    async def do_rm(self, args):
        """
        Remove a file or directory
        """
        await Client.Delete(args.paths)

    @RunSync
    async def complete_mkdir(self, text, line, begidx, endidx):
        return await self._path_completer(text, line, begidx, endidx, True)

    mkdir_parser = cmd2.Cmd2ArgumentParser()
    mkdir_parser.add_argument("path_and_son", help="path and son", default="", nargs="?", type=RunSync(Client.PathToFatherNodeAndNodeName))
    @RunSync
    @cmd2.with_argparser(mkdir_parser)
    async def do_mkdir(self, args):
        """
        Create a directory
        """
        father, sonName = args.path_and_son
        if not IsDir(father) or sonName == "" or sonName == None:
            await self.AsyncPrint("Invalid path")
            return
        child = Client.FindChildInDirByName(father, sonName)
        if child is not None:
            await self.AsyncPrint("Path already exists")
            return
        await Client.MakeDir(father, sonName)

    download_parser = cmd2.Cmd2ArgumentParser()
    download_parser.add_argument("url", help="url")
    download_parser.add_argument("path", help="path", default="", nargs="?", type=RunSync(Client.PathToNode))
    @RunSync
    @cmd2.with_argparser(download_parser)
    async def do_download(self, args):
        """
        Download a file
        """
        node = args.path
        if not IsDir(node):
            await self.AsyncPrint("Invalid directory")
            return
        task = await Client.Download(args.url, node)
        await self.AsyncPrint(f"Task {task.id} created")

    query_parser = cmd2.Cmd2ArgumentParser()
    query_parser.add_argument("-f", "--filter", help="filter", nargs="?", choices=[member.value for member in PKTaskStatus])
    @RunSync
    @cmd2.with_argparser(query_parser)
    async def do_query(self, args):
        """
        Query All Tasks
        """
        tasks = await Client.QueryTasks(PKTaskStatus(args.filter) if args.filter is not None else None)
        for task in tasks:
            await self.AsyncPrint(f"{task.id}: {task.status.name}")

async def mainLoop():
    global MainLoop, Client
    MainLoop = asyncio.get_running_loop()
    
    console = Console()
    console.preloop()
    try:
        stop = False
        while not stop:
            line = await console.AsyncInput(console.prompt)
            stop = console.onecmd_plus_hooks(line)
    finally:
        console.postloop()

if __name__ == "__main__":  
    nest_asyncio.apply()
    asyncio.run(mainLoop())
