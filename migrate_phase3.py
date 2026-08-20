from db import init_db, connect

def main():
    init_db()
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS route_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT,
            intent TEXT,
            risk TEXT,
            confidence TEXT,
            top_theme TEXT,
            primary_codes TEXT,
            route_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
    print('第三阶段数据库迁移完成：问题路由日志表已创建。')
if __name__=='__main__': main()
