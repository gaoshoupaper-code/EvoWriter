from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class Change(BaseModel):
    name: str = Field(description="已有的人物卡姓名")
    field: str = Field(description="要更新的人物现状字段，如阵营、伤势或认知")
    value: str = Field(description="本章结束时的新状态")
    reason: str = Field(description="本章发生了什么，为什么状态发生变化")


def build(abc):
    def update(name: str, field: str, value: str, reason: str) -> str:
        from app.domains.writing.creative_scope import ACTIVE, Book, valid_character_change
        from app.domains.writing.target_execution import current_execution
        task = ACTIVE.get()
        if task:
            return task.character(name, field, value, reason)
        execution = current_execution()
        if not execution or execution.stage != "text" or not execution.chapter:
            return "[拒绝] 仅正文任务可以更新人物现状"
        # Evaluation runs exclusively in the candidate's disposable task workspace.
        if not valid_character_change(field, value, reason):
            return "[拒绝] 人物字段、现状和变化原因应各为一行"
        book = Book(abc.workspace_path, evaluation=True)
        with book.tx() as db:
            book._change(db, name, field, value, execution.chapter, reason)
        book.export()
        return "已更新人物现状和变化记录"
    return StructuredTool.from_function(func=update, name="update_character_state", args_schema=Change,
                                        description="保留原有设定，更新人物现状并记录本章的变化。")
