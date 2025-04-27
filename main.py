from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.all import event_message_type, EventMessageType
from astrbot.api.message_components import Plain, Image
import yaml
import os
import re
import uuid
import time
import threading

@register("enhanced_plugin", "长安某", "关键词回复", "1.1.0", "https://github.com/your-repo-url")
class EnhancedPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        
        # 构建存储问答对的 YAML 文件路径
        self.base_path = os.path.join('data', 'plugins', 'keyword_reply')
        directory = os.path.dirname(self.base_path)
        # 若目录不存在则创建
        if not os.path.exists(directory):
            os.makedirs(directory)

        # 初始化记录状态
        self.recording = False
        self.temp_question = None
        self.temp_question_images = []
        self.just_started_recording = False
        self.current_group_id = None
        self.current_sender_id = None

        # 加载允许的群组配置
        self.allowed_groups_path = os.path.join(self.base_path, 'allowed_groups.yml')
        if not os.path.exists(self.allowed_groups_path):
            default_allowed = {"allowed_groups": []}
            with open(self.allowed_groups_path, 'w', encoding='utf-8') as f:
                yaml.dump(default_allowed, f, allow_unicode=True, indent=2)
        
        with open(self.allowed_groups_path, 'r', encoding='utf-8') as f:
            allowed_data = yaml.safe_load(f)
            self.allowed_groups = allowed_data.get('allowed_groups', [])

        # 热重载相关
        self.last_modified_time = self.get_file_modified_time(self.allowed_groups_path)
        self.hot_reload_lock = threading.Lock()
        self.hot_reload_thread = threading.Thread(target=self.hot_reload_worker)
        self.hot_reload_thread.daemon = True
        self.hot_reload_thread.start()

    def get_file_modified_time(self, file_path):
        try:
            return os.path.getmtime(file_path)
        except FileNotFoundError:
            return 0

    def hot_reload_worker(self):
        while True:
            current_modified_time = self.get_file_modified_time(self.allowed_groups_path)
            if current_modified_time > self.last_modified_time:
                with self.hot_reload_lock:
                    with open(self.allowed_groups_path, 'r', encoding='utf-8') as f:
                        allowed_data = yaml.safe_load(f)
                        self.allowed_groups = allowed_data.get('allowed_groups', [])
                    self.last_modified_time = current_modified_time
                    print("Allowed groups reloaded.")
            time.sleep(5)  # 检查间隔时间

    def get_group_triggers_path(self, group_id):
        return os.path.join(self.base_path, f'{group_id}_triggers.yml')

    def load_group_triggers(self, group_id):
        triggers_path = self.get_group_triggers_path(group_id)
        if not os.path.exists(triggers_path):
            with open(triggers_path, 'w', encoding='utf-8') as f:
                yaml.dump({"triggers": {}}, f, allow_unicode=True, indent=2)
        with open(triggers_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            return data.get('triggers', {})

    @filter.command("开始记录")
    async def start_recording(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        group_id = message_obj.group_id
        sender_id = event.get_sender_id()
        
        # 检查是否来自允许的群组
        if str(group_id) not in self.allowed_groups:
            return
        
        # 避免不同群或用户同时记录
        if self.recording and (self.current_group_id != group_id or self.current_sender_id != sender_id):
            return

        # 加载当前群组的触发词
        self.triggers = self.load_group_triggers(group_id)
        self.yaml_path = self.get_group_triggers_path(group_id)

        # 开启记录状态
        self.recording = True
        self.temp_question = None
        self.temp_question_images = []
        self.just_started_recording = True
        self.current_group_id = group_id
        self.current_sender_id = sender_id
        yield event.make_result().message("已开始记录，请输入问题。")

    @event_message_type(EventMessageType.ALL)
    async def handle_all_messages(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        group_id = message_obj.group_id
        sender_id = event.get_sender_id()
        message_str = event.message_str
        message_chain = message_obj.message

        # 检查是否来自允许的群组
        if str(group_id) not in self.allowed_groups:
            return

        # 加载当前群组的触发词
        self.triggers = self.load_group_triggers(group_id)
        self.yaml_path = self.get_group_triggers_path(group_id)

        if self.recording:
            # 忽略非当前记录群或用户的消息
            if self.current_group_id != group_id or self.current_sender_id != sender_id:
                return

            if self.just_started_recording:
                if message_str == "开始记录":
                    return
                # 记录问题文本和图片信息
                self.temp_question = message_str
                for component in message_chain:
                    if isinstance(component, Image):
                        self.temp_question_images.append(component.url if hasattr(component, 'url') else component.file)
                self.just_started_recording = False
                yield event.make_result().message("问题已记录，请输入答案。")
            elif self.temp_question:
                answer_text = message_str
                answer_images = []
                # 记录答案文本和图片信息
                for component in message_chain:
                    if isinstance(component, Image):
                        answer_images.append(component.url if hasattr(component, 'url') else component.file)

                question = {"text": self.temp_question, "images": self.temp_question_images}
                answer = {"text": answer_text, "images": answer_images}
                # 使用唯一标识符作为键
                unique_id = str(uuid.uuid4())
                self.triggers[unique_id] = {"question": question, "answer": answer}

                # 保存问答对到 YAML 文件
                data = {"triggers": self.triggers}
                with open(self.yaml_path, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f, allow_unicode=True, indent=2)

                # 结束记录状态
                self.recording = False
                self.temp_question = None
                self.temp_question_images = []
                self.just_started_recording = False
                self.current_group_id = None
                self.current_sender_id = None
                yield event.make_result().message("答案已记录，新的问答对已保存。")
        else:
            # 非记录状态下，使用正则匹配消息并回复
            for unique_id, qa_pair in self.triggers.items():
                question = qa_pair["question"]
                answer = qa_pair["answer"]
                pattern = question["text"].replace("%", ".*")
                if re.search(pattern, message_str):
                    reply_chain = []
                    if answer["text"]:
                        reply_chain.append(Plain(text=answer["text"]))
                    for image_url in answer["images"]:
                        reply_chain.append(Image.fromURL(url=image_url))
                    yield event.chain_result(reply_chain)
                    return
            if message_str == "开始记录":
                return

    @filter.command("查看关键词")
    async def view_keywords(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        group_id = message_obj.group_id
        
        # 检查是否来自允许的群组
        if str(group_id) not in self.allowed_groups:
            return

        # 加载当前群组的触发词
        self.triggers = self.load_group_triggers(group_id)

        # 若没有关键词，提示用户
        if not self.triggers:
            yield event.make_result().message("当前没有记录任何关键词。")
        else:
            keyword_list = []
            for unique_id, qa_pair in self.triggers.items():
                question = qa_pair["question"]
                keyword_list.append(question["text"])
            keyword_text = "\n".join(keyword_list)
            # 发送关键词列表给用户
            yield event.make_result().message(f"当前记录的关键词如下：\n{keyword_text}")

    @filter.command("删除关键词")
    async def delete_keyword(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        group_id = message_obj.group_id
        
        # 检查是否来自允许的群组
        if str(group_id) not in self.allowed_groups:
            return

        message_str = event.message_str
        parts = message_str.split(" ", 1)
        if len(parts) < 2:
            # 提示用户正确的删除关键词格式
            yield event.make_result().message("请提供要删除的关键词，格式为：删除关键词 具体关键词")
            return

        keyword = parts[1]
        # 加载当前群组的触发词
        self.triggers = self.load_group_triggers(group_id)
        self.yaml_path = self.get_group_triggers_path(group_id)

        target_unique_id = None
        # 查找要删除的关键词
        for unique_id, qa_pair in self.triggers.items():
            question = qa_pair["question"]
            if question["text"] == keyword:
                target_unique_id = unique_id
                break

        if target_unique_id:
            # 删除关键词及其对应答案
            del self.triggers[target_unique_id]
            data = {"triggers": self.triggers}
            with open(self.yaml_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, indent=2)
            yield event.make_result().message(f"关键词 '{keyword}' 及其回复信息已成功删除。")
        else:
            yield event.make_result().message(f"未找到关键词 '{keyword}'。")

    @filter.command("添加群组")
    async def add_group(self, event: AstrMessageEvent):
        message_str = event.message_str
        parts = message_str.split(" ", 1)
        if len(parts) < 2:
            yield event.make_result().message("请提供要添加的群号，格式为：添加群组 群号")
            return
        
        group_id = parts[1]
        if group_id not in self.allowed_groups:
            self.allowed_groups.append(group_id)
            data = {"allowed_groups": self.allowed_groups}
            with open(self.allowed_groups_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, indent=2)
            yield event.make_result().message(f"已添加群组 {group_id} 到允许列表。")
        else:
            yield event.make_result().message(f"群组 {group_id} 已在允许列表中。")

    @filter.command("删除群组")
    async def remove_group(self, event: AstrMessageEvent):
        message_str = event.message_str
        parts = message_str.split(" ", 1)
        if len(parts) < 2:
            yield event.make_result().message("请提供要删除的群号，格式为：删除群组 群号")
            return
        
        group_id = parts[1]
        if group_id in self.allowed_groups:
            self.allowed_groups.remove(group_id)
            data = {"allowed_groups": self.allowed_groups}
            with open(self.allowed_groups_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, indent=2)
            # 删除该群组的触发词文件
            triggers_path = self.get_group_triggers_path(group_id)
            if os.path.exists(triggers_path):
                os.remove(triggers_path)
            yield event.make_result().message(f"已将群组 {group_id} 从允许列表中移除。")
        else:
            yield event.make_result().message(f"群组 {group_id} 不在允许列表中。")

    @filter.command("查看允许的群组")
    async def view_allowed_groups(self, event: AstrMessageEvent):
        if not self.allowed_groups:
            yield event.make_result().message("当前没有允许的群组。")
        else:
            groups_text = "\n".join(self.allowed_groups)
            yield event.make_result().message(f"当前允许的群组如下：\n{groups_text}")
