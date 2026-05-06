import os
import requests
from dotenv import load_dotenv
from rich.console import Console

console = Console()

def verify_notion_connection():
    load_dotenv()
    
    api_key = os.getenv("NOTION_API_KEY")
    db_id = os.getenv("NOTION_DATABASE_ID")
    
    if not api_key or not db_id:
        console.print("[bold red]Error:[/bold red] NOTION_API_KEY or NOTION_DATABASE_ID missing from environment variables.")
        return False
        
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": "2022-06-28"
    }
    
    console.print(f"[cyan]Testing connection to Notion Database: {db_id}[/cyan]")
    
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            db_info = response.json()
            db_title = db_info.get("title", [{}])[0].get("plain_text", "Unknown Title") if db_info.get("title") else "Unknown Title"
            console.print(f"[bold green]Success![/bold green] Connected to database: '{db_title}'")
            return True
        else:
            console.print(f"[bold red]Failed to connect![/bold red] Status Code: {response.status_code}")
            console.print(f"Response: {response.text}")
            return False
    except Exception as e:
        console.print(f"[bold red]Exception occurred:[/bold red] {e}")
        return False

if __name__ == "__main__":
    verify_notion_connection()
