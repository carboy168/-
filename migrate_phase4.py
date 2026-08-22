from project_mode import ensure_project_schema, seed_standard_aliases

def main():
    ensure_project_schema()
    seed_standard_aliases()
    print('V1.0第四阶段数据库迁移完成：项目档案、项目要求、当前项目设置、项目问答日志、广东标准双编号别名已就绪。')

if __name__=='__main__':
    main()
