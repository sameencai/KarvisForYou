killall python3
sleep 2
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 服务重启 =====" >> karvis.log
nohup python3 app.py >> karvis.log 2>&1 &
