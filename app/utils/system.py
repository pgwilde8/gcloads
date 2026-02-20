import psutil

def get_system_stats():
    # Grab real CPU and Memory usage from the Linux kernel
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    
    # Calculate AI spend (mock logic for now, connecting to your DB logs later)
    # This tracks how many "Premium" API calls you made vs Local ones
    ai_spend = 0.04  # Example: 4 cents today
    
    return {
        "cpu_usage": cpu,
        "ram_usage": ram,
        "ai_spend": f"{ai_spend:.2f}"
    }