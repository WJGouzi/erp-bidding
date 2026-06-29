from app import create_app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=23112, debug=app.config.get("DEBUG", False))




# 启动整个服务的命令
# lsof -ti :23112 | xargs kill -9; cd /Users/wangjun/Desktop/work/erp/code/erp-bidding && source .venv/bin/activate && python3 run.py