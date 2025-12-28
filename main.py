# main.py
from brain import handle_user_message

user_id = "3681e084-8411-4418-9983-40f15ae9760d"

print("Portfolio AI is ready. Type your message below:")

while True:
    message = input("You: ")
    if message.lower() in ["exit", "quit"]:
        break
    reply = handle_user_message(user_id, message)
    print(f"Portfolio AI: {reply}")
