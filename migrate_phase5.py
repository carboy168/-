from project_kb import ensure_project_kb_schema
from project_mode import ensure_project_schema, seed_standard_aliases
from db import init_db

def main():
    init_db();ensure_project_schema();seed_standard_aliases();ensure_project_kb_schema()
    print('V1.0第五阶段迁移完成：项目文件库、中文全文索引、审查任务和整改问题表已创建。')

if __name__=='__main__':
    main()
