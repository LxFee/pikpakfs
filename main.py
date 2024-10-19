from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Input, Log
from textual.containers import Horizontal, Vertical, Widget
from collections import deque
import sys
import asyncio
import argparse
import pikpakFs
import logging
import functools

class TextualLogHandler(logging.Handler):
    def __init__(self, log_widget: Log):
        super().__init__()
        self.log_widget = log_widget

    def emit(self, record):
        message = self.format(record)
        self.log_widget.write_line(message)

class HistoryInput(Input):
    def __init__(self, placeholder: str = "", max_history: int = 20, *args, **kwargs):
        super().__init__(placeholder=placeholder, *args, **kwargs)
        self.block_input = False
        self.history = deque(maxlen=max_history)  # 历史记录列表
        self.history_view = list()
        self.history_index = -1  # 当前历史索引，初始为 -1
        self.history_log = Log(auto_scroll=False)  # 用于显示历史记录的日志小部件

    def widget(self) -> Widget:
        return Vertical(self, self.history_log)

    def reverseIdx(self, idx) -> int:
        return len(self.history) - 1 - idx

    async def on_key(self, event: events.Key) -> None:
        if self.block_input:
            return 
        if event.key == "up":
            if self.history_index == -1:
                self.cursor_position = len(self.value)
                await self.update_history_view()
                return
            self.history_index = max(0, self.history_index - 1)
        elif event.key == "down":
            self.history_index = min(len(self.history) - 1, self.history_index + 1)
        else:
            self.history_index = -1
            await self.update_history_view()
            return
        
        if len(self.history) > 0 and self.history_index != -1:
            self.value = self.history[self.reverseIdx(self.history_index)]
        self.cursor_position = len(self.value)
        await self.update_history_view()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value.strip()
        if user_input:
            self.history.append(user_input)
        self.history_index = -1
        self.value = ""
        await self.update_history_view()

    async def update_history_view(self):
        self.history_log.clear()
        self.history_view.clear()

        if self.history:
            for idx, item in enumerate(self.history):
                prefix = "> " if self.reverseIdx(idx) == self.history_index else "  "
                self.history_view.append(f"{prefix}{item}")
        
        self.history_log.write_lines(reversed(self.history_view))
        
        scroll_height = self.history_log.scrollable_size.height
        scroll_start = self.history_log.scroll_offset.y
        current = self.history_index

        if current < scroll_start:
            scroll_idx = min(max(0, current), len(self.history) - 1)
            self.history_log.scroll_to(y = scroll_idx)
        elif current >= scroll_start + scroll_height - 1:
            self.history_log.scroll_to(y = current - scroll_height + 1)

        self.refresh()
    
    async def animate_ellipsis(self):
        ellipsis = ""
        try:
            while True:
                # 循环添加省略号（最多3个点）
                if len(ellipsis) < 3:
                    ellipsis += "."
                else:
                    ellipsis = ""
                self.value = f"Waiting{ellipsis}"
                await asyncio.sleep(0.5)
        finally:
            self.value = ""
            pass
    
    async def wait_for(self, operation):
        self.disabled = True
        self.block_input = True
        animation_task = asyncio.create_task(self.animate_ellipsis())
        await operation()
        animation_task.cancel()
        self.disabled = False
        self.block_input = False
        self.focus()


class InputLoggerApp(App):
    CSS = """
    .divider {
        width: 0.5%;
        height: 100%;
        background: #444444;
    }
    .log {
        width: 80%;
        height: 100%;
    }
    """  
    
    def setup_logger(self) -> None:
        formatStr = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

        logging.basicConfig(
            filename='app.log',
            filemode='a',
            format=formatStr
        )

        logHandler = TextualLogHandler(self.log_widget)

        # 设置日志格式
        logHandler.setFormatter(logging.Formatter(formatStr))

        # 获取根日志记录器，并添加自定义处理器
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(logHandler)

    def write_to_console(self, content) -> None:
        self.log_widget.write_line(content)

    def compose(self) -> ComposeResult:
        self.input_widget = HistoryInput(placeholder="Input Command...")
        self.log_widget = Log(classes="log", highlight=True)

        left_panel = self.input_widget.widget()   
        right_panel = self.log_widget
        divider = Vertical(classes="divider")

        yield Horizontal(left_panel, divider, right_panel)
    
    def on_mount(self) -> None:
        self.setup_logger()
        self.fs = pikpakFs.VirtFs("", "", "", loginCachePath = "token.json")

    async def handle_command(self, command) -> None:
        try:
            if command == "clear":
                self.log_widget.clear()
            elif command == "exit":
                sys.exit(0)
            elif command == "debug":
                logger = logging.getLogger()
                logger.setLevel(logging.DEBUG)
                self.write_to_console("Done")
            else:
                self.write_to_console(await self.fs.HandlerCommand(command))
        except Exception as e:
            logging.exception(e)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self.input_widget:
            return
        
        user_input = event.value.strip()
        self.write_to_console(f"> {user_input}")
        await self.input_widget.wait_for(functools.partial(self.handle_command, user_input))

if __name__ == "__main__":
    app = InputLoggerApp()
    app.run()
