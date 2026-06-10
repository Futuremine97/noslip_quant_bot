import getpass
import socket
import sys

def check_machine_auth():
    username = getpass.getuser()
    hostname = socket.gethostname()
    
    # Authorized developer info: sunghoon on Sunghoonss-MacBook-Air
    is_auth_user = (username == "sunghoon")
    is_auth_host = ("Sunghoonss-MacBook-Air" in hostname or hostname.endswith(".local") or "Mac" in hostname)
    
    if not (is_auth_user and is_auth_host):
        print(f"❌ [Security Alert] Unauthorized machine/user detected: {username}@{hostname}")
        print("This project is configured to run exclusively on sunghoon@Sunghoonss-MacBook-Air.")
        print("Execution blocked to protect trading credentials and API keys.")
        sys.exit(1)

# Run the check immediately on module import
check_machine_auth()
