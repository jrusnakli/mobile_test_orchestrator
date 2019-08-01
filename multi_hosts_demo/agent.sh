export ANDROID_HOME=/home/userhelen/android_sdk
export ANDROID_SDK=/home/userhelen/android_sdk
export ANDROID_SDK_ROOT=$ANDROID_HOME
export EMULATOR_OPTS="-no-window -no-audio"
export PATH=$ANDROID_SDK/emulator:$ANDROID_SDK/tools/bin:$PATH

source /home/userhelen/venv/bin/activate
mkdir -p /home/userhelen/mto_work_dir

while true
        do
            if [ -e /home/userhelen/mto_work_dir/*.zip ] && [ -e /home/userhelen/mto_work_dir/new_job_flag ]
            then
                unzip /home/userhelen/mto_work_dir/*.zip -d /home/userhelen/mto_work_dir
                python masterserver.py /home/userhelen/mto_work_dir/work_file/args.txt > log_file
                rm -rf /home/userhelen/mto_work_dir/*.zip
                rm -rf /home/userhelen/mto_work_dir/work_file
                rm -rf /home/userhelen/mto_work_dir/new_job_flag
            fi
        done