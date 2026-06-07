"""Question generator using Claude Code CLI (subprocess).

Works with sections (not whole notes) — each section is a self-contained
knowledge unit for question generation.
"""

from __future__ import annotations

import json
import re
import subprocess

from .models import Note, Option, Question, QuestionType, new_id, now

# ---------------------------------------------------------------------------
# JSON Schema for structured output
# ---------------------------------------------------------------------------

QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["multiple_choice", "single_choice", "true_false", "programming", "short_answer", "fill_blank"],
                    },
                    "topic": {"type": "string"},
                    "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "text": {"type": "string"},
                            },
                            "required": ["id", "text"],
                        },
                    },
                    "code_context": {"type": "string"},
                    "correct_answer": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["type", "topic", "difficulty", "question", "correct_answer", "explanation"],
            },
        }
    },
    "required": ["questions"],
}


SYSTEM_PROMPT = """你是一位资深教学设计师，专精于设计高质量练习题。你遵循认知科学原则：

1. **原子化** — 每道题只测一个明确的知识点，不堆砌
2. **测试理解而非记忆** — 题目考察"为什么"和"怎么样"，不是死记硬背
3. **干扰项要有教学价值** — 错误选项反映常见误解，不是随机拼凑
4. **解释即教学** — 每道题的解释要让人看完就懂，不只是说"XX是对的"
5. **基于真实内容** — 题目只能来自给定的笔记内容，不能编造知识点
6. **难度递进** — easy=直接回忆, medium=理解应用, hard=分析综合"""


def _build_prompt(
    sections_text: str,
    count: int,
    question_types: list[QuestionType],
    difficulty: str,
    topic: str | None = None,
) -> str:
    type_desc = {
        "multiple_choice": "单选题",
        "true_false": "判断题",
        "programming": "编程题",
        "short_answer": "简答题",
        "fill_blank": "填空题",
    }
    types_str = "、".join(type_desc.get(t, t) for t in question_types)

    # Few-shot examples based on question type
    examples = _get_few_shot_examples(question_types)

    return f"""请根据以下笔记内容，生成 {count} 道{types_str}。

整体难度：{difficulty}
{f"聚焦主题：{topic}" if topic else ""}

## 出题步骤（请先思考，再输出题目）

第一步：从笔记中提取 {count} 个核心知识点
第二步：为每个知识点设计一道题（easy=直接回忆, medium=理解应用, hard=分析综合）
第三步：输出 JSON

## 出题规范

### 选择题 (multiple_choice)
- 4个选项，只有1个正确答案
- 干扰项必须合理（常见错误、概念混淆），不能明显荒谬
- 选项长度相近，避免"最长的是答案"

### 判断题 (true_false)
- 不能出"总是""从不"之类绝对化陷阱
- 错误陈述要反映真实的常见误解

### 编程题 (programming)
- 提供代码上下文 (code_context)
- 要求补全关键逻辑 或 指出并修复 bug
- 不要出"写一个完整程序"这种开放式题目

### 简答题 (short_answer)
- 问"为什么""怎么用""有什么区别"
- 不问"XX是什么"（那是填空题的事）

### 填空题 (fill_blank)
- 填关键术语、具体数值、核心概念
- 一句话只留1个空

## 参考示例

{examples}

## 笔记内容

{sections_text}

请输出 JSON 对象，questions 数组。严格符合指定 JSON Schema。"""


def _get_few_shot_examples(question_types: list[QuestionType]) -> str:
    """Return few-shot examples for the requested question types."""
    examples = []

    if "multiple_choice" in question_types:
        examples.append("""【选择题示例】
笔记内容："Python 的 tuple 是不可变类型，一旦创建就不能修改元素。"
生成的题目：
{{
  "type": "multiple_choice",
  "topic": "Python 元组",
  "difficulty": "easy",
  "question": "在 Python 中，关于 tuple（元组）的说法正确的是？",
  "options": [
    {{"id": "a", "text": "tuple 创建后可以增删元素，但不能修改已有元素"}},
    {{"id": "b", "text": "tuple 一旦创建，不能增删改任何元素"}},
    {{"id": "c", "text": "tuple 和 list 完全一样，只是写法不同"}},
    {{"id": "d", "text": "tuple 可以修改元素，但不能改变长度"}}
  ],
  "correct_answer": "b",
  "explanation": "tuple 是不可变类型（immutable），一旦创建就不能做任何修改——不能增删元素，也不能修改已有元素的值。a错在'可以增删'，c错在忽略了可变性的核心区别，d完全相反。"
}}""")

    if "programming" in question_types:
        examples.append("""【编程题示例】
笔记内容："在 C++ 中，std::unique_ptr 独占对象所有权，不能拷贝只能移动。"
生成的题目：
{{
  "type": "programming",
  "topic": "C++ 智能指针",
  "difficulty": "medium",
  "question": "以下代码有什么问题？请指出错误并写出修正后的代码。",
  "code_context": "auto p1 = std::make_unique<int>(42);\\nauto p2 = p1;",
  "correct_answer": "unique_ptr 不可拷贝。修正：auto p2 = std::move(p1); 此后 p1 变为 nullptr。",
  "explanation": "std::unique_ptr 独占所有权，拷贝构造函数被删除。需要转移所有权时必须使用 std::move()。这是 C++ 防止双重释放的设计。"
}}""")

    if "short_answer" in question_types:
        examples.append("""【简答题示例】
笔记内容："chmod 755 设置权限为 rwxr-xr-x，数字 7=4+2+1 即 rwx。"
生成的题目：
{{
  "type": "short_answer",
  "topic": "Linux 权限管理",
  "difficulty": "medium",
  "question": "chmod 755 script.sh 执行后，三类用户（所有者、同组、其他）分别有什么权限？数字 7、5、5 为什么对应这些权限？",
  "correct_answer": "所有者 rwx（读写执行），同组 r-x（读执行），其他 r-x（读执行）。数字含义：r=4, w=2, x=1。7=4+2+1(rwx)，5=4+1(r-x)。",
  "explanation": "Linux 权限用三位八进制数字表示：第一位所有者，第二位同组，第三位其他。每位的数字是 r(4)+w(2)+x(1) 的和。"
}}""")

    if "true_false" in question_types:
        examples.append("""【判断题示例】
笔记内容："C++ 引用必须在声明时初始化，且初始化后不能改变绑定。"
生成的题目：
{{
  "type": "true_false",
  "topic": "C++ 引用",
  "difficulty": "easy",
  "question": "C++ 引用声明后可以不立即初始化，后续再绑定到变量。",
  "correct_answer": "错误",
  "explanation": "C++ 引用必须在声明时立即初始化，且一旦绑定到某个变量后，就不能再改为绑定其他变量。这是引用与指针的重要区别。"
}}""")

    if "fill_blank" in question_types:
        examples.append("""【填空题示例】
笔记内容："Python 是动态类型语言，变量不需要声明类型。"
生成的题目：
{{
  "type": "fill_blank",
  "topic": "Python 类型系统",
  "difficulty": "easy",
  "question": "Python 是_____类型语言，变量在运行时自动确定类型，无需提前声明。",
  "correct_answer": "动态",
  "explanation": "Python 是动态类型语言，类型检查发生在运行时。相对地，C++/Java 是静态类型语言，编译时检查类型。"
}}""")

    return "\n".join(examples) if examples else ""


def _extract_json(text: str) -> list[dict]:
    """Extract questions array from Claude Code's response."""
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    data = json.loads(text)

    if isinstance(data, dict):
        if "structured_output" in data:
            data = data["structured_output"]
        if isinstance(data, dict) and "questions" in data:
            return data["questions"]

    if isinstance(data, list):
        return data

    return []


# ---------------------------------------------------------------------------
# Section → text helpers
# ---------------------------------------------------------------------------

def sections_to_text(sections: list[dict]) -> str:
    """Convert a list of section dicts to a compact prompt text."""
    parts: list[str] = []
    for sec in sections:
        note_title = sec.get("note_path", "").replace(".md", "").replace("_", " ").title()
        if sec["heading"]:
            parts.append(f"## [{note_title}] {sec['heading']}")
        else:
            parts.append(f"## [{note_title}]")
        if sec["content"]:
            parts.append(sec["content"][:2000])
        for cb in sec.get("code_blocks", []):
            parts.append(f"```\n{cb[:1500]}\n```")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main generator — uses claude CLI
# ---------------------------------------------------------------------------

class QuestionGenerator:
    """Generate questions from note sections using Claude Code CLI."""

    def __init__(self, model: str = "sonnet"):
        self.model = model

    def generate_from_sections(
        self,
        sections: list[dict],
        count: int = 10,
        question_types: list[QuestionType] | None = None,
        difficulty: str = "mixed",
        topic: str | None = None,
    ) -> list[Question]:
        """Generate questions from a list of section dicts (from storage).

        Each section dict has: id, note_path, heading, level, content, code_blocks.

        Returns list of Question objects with source_note and source_section populated.
        """
        if question_types is None:
            question_types = ["multiple_choice"]

        sections_text = sections_to_text(sections)
        if not sections_text.strip():
            raise ValueError("No section content available to generate questions.")

        prompt = _build_prompt(sections_text, count, question_types, difficulty, topic)

        # Combine system prompt with user prompt
        full_prompt = SYSTEM_PROMPT + "\n\n---\n\n" + prompt

        cmd = [
            "claude", "-p", full_prompt,
            "--model", self.model,
            "--output-format", "json",
            "--json-schema", json.dumps(QUESTION_SCHEMA),
            "--max-budget-usd", "1",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            raw_text = result.stdout + result.stderr
            if not raw_text.strip():
                raise RuntimeError(f"claude CLI failed (exit {result.returncode}): {result.stderr[:300]}")
        else:
            raw_text = result.stdout

        raw_questions = _extract_json(raw_text)

        questions: list[Question] = []
        # Use the first section's note as primary source
        primary_note = sections[0]["note_path"] if sections else ""

        type_aliases = {
            "single_choice": "multiple_choice",
            "choice": "multiple_choice",
            "multi_choice": "multiple_choice",
        }

        for rq in raw_questions:
            qtype = rq.get("type", "multiple_choice")
            qtype = type_aliases.get(qtype, qtype)
            if qtype not in question_types:
                continue

            options = None
            if qtype in ("multiple_choice",) and "options" in rq:
                options = [Option(id=o["id"], text=o["text"]) for o in rq["options"]]

            difficulty_val = rq.get("difficulty", "medium")
            if difficulty_val not in ("easy", "medium", "hard"):
                difficulty_val = "medium"

            q = Question(
                id=new_id(),
                type=qtype,
                topic=rq.get("topic", ""),
                difficulty=difficulty_val,
                question=rq.get("question", ""),
                options=options,
                code_context=rq.get("code_context"),
                correct_answer=str(rq.get("correct_answer", "")),
                explanation=rq.get("explanation", ""),
                source_note=primary_note,
                source_section=sections[0]["id"],  # primary section
                created_at=now(),
            )
            questions.append(q)

        return questions

    # Legacy API — kept for CLI backward compat
    def generate(
        self,
        notes: list[Note],
        count: int = 10,
        question_types: list[QuestionType] | None = None,
        difficulty: str = "mixed",
        topic: str | None = None,
    ) -> list[Question]:
        """Generate questions from Note objects (legacy, prefers generate_from_sections)."""
        if question_types is None:
            question_types = ["multiple_choice"]

        # Convert notes to section-like dicts
        sections: list[dict] = []
        for note in notes:
            if topic:
                if not (topic in note.tags or topic.lower() in note.title.lower()):
                    continue
            for s in note.sections:
                sections.append({
                    "id": s.id,
                    "note_path": note.path,
                    "heading": s.heading,
                    "level": s.level,
                    "content": s.content,
                    "code_blocks": s.code_blocks,
                })

        if not sections:
            raise ValueError("No note content available to generate questions.")

        return self.generate_from_sections(sections, count, question_types, difficulty, topic)

    def generate_batch(
        self,
        notes: list[Note],
        count: int = 10,
        question_types: list[QuestionType] | None = None,
        difficulty: str = "mixed",
        topic: str | None = None,
    ) -> list[Question]:
        if count <= 10:
            return self.generate(notes, count, question_types, difficulty, topic)

        all_questions: list[Question] = []
        remaining = count
        while remaining > 0:
            batch_count = min(remaining, 10)
            questions = self.generate(notes, batch_count, question_types, difficulty, topic)
            all_questions.extend(questions)
            remaining -= batch_count
        return all_questions

    def generate_batch_from_sections(
        self,
        sections: list[dict],
        count: int = 10,
        question_types: list[QuestionType] | None = None,
        difficulty: str = "mixed",
        topic: str | None = None,
    ) -> list[Question]:
        if count <= 10:
            return self.generate_from_sections(sections, count, question_types, difficulty, topic)

        all_questions: list[Question] = []
        remaining = count
        while remaining > 0:
            batch_count = min(remaining, 10)
            questions = self.generate_from_sections(sections, batch_count, question_types, difficulty, topic)
            all_questions.extend(questions)
            remaining -= batch_count
        return all_questions
