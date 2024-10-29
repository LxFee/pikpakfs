import asyncio, nest_asyncio
import cmd2
from functools import wraps
import logging
import threading
import colorlog
from pikpakFs import VirtFsNode, DirNode, FileNode, PKVirtFs, IsDir, IsFile
import os

def RunSyncInLoop(loop):
    def decorator(func):
        @wraps(func)
        def decorated(*args, **kwargs):
            currentLoop = None
            try:
                currentLoop = asyncio.get_running_loop()
            except RuntimeError:
                pass
            if currentLoop is loop:
                return loop.run_until_complete(func(*args, **kwargs))
            else:
                return asyncio.run_coroutine_threadsafe(func(*args, **kwargs), loop).result()
        return decorated
    return decorator

def ProvideDecoratorSelfArgs(decorator, argsProvider):
    def wrapper(func):
        @wraps(func)
        def decorated(*args, **kwargs):
            namespace = args[0]
            return decorator(argsProvider(namespace))(func)(*args, **kwargs)
        return decorated
    return wrapper

class PikpakConsole(cmd2.Cmd):
    def LoopProvider(self):
        return self.loop

    RunSync = ProvideDecoratorSelfArgs(RunSyncInLoop, LoopProvider)
    
    def _setup_logging(self):
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

    def IOWorker(self, loop):
        self.terminal_lock.acquire() # 我看cmdloop是这么做的，所以我也这么做
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            self.terminal_lock.release()

    async def ainput(self, prompt):
        async def ReadInput(prompt):
            return self._read_command_line(prompt)
        future = asyncio.run_coroutine_threadsafe(ReadInput(prompt), self.ioLoop)
        return await asyncio.wrap_future(future)

    async def aoutput(self, output):
        async def PrintOuput(output):
            print(output)
        future = asyncio.run_coroutine_threadsafe(PrintOuput(output), self.ioLoop)
        await asyncio.wrap_future(future)

    def ParserProvider(self):
        return cmd2.Cmd2ArgumentParser()

    def AddPathParser(parserProvider, nargs = "?"):
        def PathParserProvider(self):
            parser = parserProvider(self)
            parser.add_argument("path", help="path", default="", nargs=nargs, type=RunSyncInLoop(self.loop)(self.client.PathToNode))
            return parser
        return PathParserProvider
    
    def AddFatherAndSonParser(parserProvider):
        def PathParserProvider(self):
            parser = parserProvider(self)
            parser.add_argument("path", help="path", default="", nargs="?", type=RunSyncInLoop(self.loop)(self.client.PathToFatherNodeAndNodeName))
            return parser
        return PathParserProvider

    def AddUrlParser(parserProvider):
        def PathParserProvider(self):
            parser = parserProvider(self)
            parser.add_argument("url", help="url")
            return parser
        return PathParserProvider
    
    def AddUsernamePasswordParser(parserProvider):
        def PathParserProvider(self):
            parser = parserProvider(self)
            parser.add_argument("username", help="username", nargs="?")
            parser.add_argument("password", help="password", nargs="?")
            return parser
        return PathParserProvider

    async def PathCompleter(self, text, line, begidx, endidx, filterFiles):   
        father, sonName = await self.client.PathToFatherNodeAndNodeName(text)
        if not IsDir(father):
            return []
        matches = []
        matchesNode = []
        for childId in father.childrenId:
            child = self.client.GetNodeById(childId)
            if filterFiles and IsFile(child):
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

    def __init__(self):
        super().__init__()
        self._setup_logging()
        self.client = PKVirtFs("token.json", proxy="http://127.0.0.1:7897")

    async def Run(self):
        # 1. 设置忽略SIGINT
        import signal
        def signal_handler(sig, frame):
            pass
        signal.signal(signal.SIGINT, signal_handler)

        # 2. 创建一个新的事件循环
        self.loop = asyncio.get_running_loop()
        self.ioLoop = asyncio.new_event_loop()
        thread = threading.Thread(target=self.IOWorker, args=(self.ioLoop,))
        thread.start()

        # 3. 启动cmd2
        saved_readline_settings = None
        try:
            # Get sigint protection while we set up readline for cmd2
            with self.sigint_protection:
                saved_readline_settings = self._set_up_cmd2_readline()

            stop = False
            while not stop:
                # Get sigint protection while we read the command line
                line = await self.ainput(self.prompt)
                # Run the command along with all associated pre and post hooks
                stop = self.onecmd_plus_hooks(line)
        finally:
            # Get sigint protection while we restore readline settings
            with self.sigint_protection:
                if saved_readline_settings is not None:
                    self._restore_readline(saved_readline_settings)
            # https://stackoverflow.com/questions/51642267/asyncio-how-do-you-use-run-forever
            self.ioLoop.call_soon_threadsafe(self.ioLoop.stop)
            thread.join()
    
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

    @RunSync
    @ProvideDecoratorSelfArgs(cmd2.with_argparser, AddUsernamePasswordParser(ParserProvider))
    async def do_login(self, args):
        """
        Login to pikpak
        """
        await self.client.Login(args.username, args.password)
        await self.aoutput("Logged in successfully")

    @RunSync
    async def complete_ls(self, text, line, begidx, endidx):
        return await self.PathCompleter(text, line, begidx, endidx, filterFiles = False)

    @RunSync
    @ProvideDecoratorSelfArgs(cmd2.with_argparser, AddPathParser(ParserProvider))
    async def do_ls(self, args):
        """
        List files in a directory
        """
        node = args.path
        if node is None:
            await self.aoutput("Invalid path")
            return
        await self.client.Refresh(node)
        if IsDir(node):
            for childId in node.childrenId:
                child = self.client.GetNodeById(childId)
                await self.aoutput(child.name)
        elif IsFile(node):
            await self.aoutput(f"{node.name}: {node.url}")            
    
    @RunSync
    async def complete_cd(self, text, line, begidx, endidx):
        return await self.PathCompleter(text, line, begidx, endidx, filterFiles = True)

    @RunSync
    @ProvideDecoratorSelfArgs(cmd2.with_argparser, AddPathParser(ParserProvider))
    async def do_cd(self, args):
        """
        Change directory
        """
        node = args.path
        if not IsDir(node):
            await self.aoutput("Invalid directory")
            return
        self.client.currentLocation = node
    
    @RunSync
    async def do_cwd(self, args):
        """
        Print current working directory
        """
        await self.aoutput(self.client.NodeToPath(self.client.currentLocation))

    def do_clear(self, args):
        """
        Clear the terminal screen
        """
        os.system('cls' if os.name == 'nt' else 'clear')

    @RunSync
    async def complete_rm(self, text, line, begidx, endidx):
        return await self.PathCompleter(text, line, begidx, endidx, filterFiles = False)

    @RunSync
    @ProvideDecoratorSelfArgs(cmd2.with_argparser, AddPathParser(ParserProvider, "+"))
    async def do_rm(self, args):
        """
        Remove a file or directory
        """
        await self.client.Delete(args.path)

    @RunSync
    async def complete_mkdir(self, text, line, begidx, endidx):
        return await self.PathCompleter(text, line, begidx, endidx, filterFiles = True)

    @RunSync
    @ProvideDecoratorSelfArgs(cmd2.with_argparser, AddFatherAndSonParser(ParserProvider))
    async def do_mkdir(self, args):
        """
        Create a directory
        """
        father, sonName = args.path
        if not IsDir(father) or sonName == "" or sonName == None:
            await self.aoutput("Invalid path")
            return
        child = self.client.FindChildInDirByName(father, sonName)
        if child is not None:
            await self.aoutput("Path already exists")
            return
        await self.client.MakeDir(father, sonName)

    @RunSync
    @ProvideDecoratorSelfArgs(cmd2.with_argparser, AddPathParser(AddUrlParser(ParserProvider)))
    async def do_download(self, args):
        """
        Download a file
        """
        node = args.path
        if not IsDir(node):
            await self.aoutput("Invalid directory")
            return
        await self.client.Download(args.url, node)


if __name__ == "__main__":  
    nest_asyncio.apply()
    prog = PikpakConsole()
    asyncio.run(prog.Run())
