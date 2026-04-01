# app.py
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from datetime import datetime
from typing import Dict, List, Any, Optional
import requests
from requests import Session
from hashlib import sha256
from time import time as timestamp
import secrets
import re
import humanize

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
CORS(app)


def generate_website_token(user_agent: str, account_token: str) -> str:
    """Generate the dynamic X-Website-Token required by GoFile API."""
    time_slot = int(timestamp()) // 14400
    raw = f"{user_agent}::en-US::{account_token}::{time_slot}::5d4f7g8sd45fsd"
    return sha256(raw.encode()).hexdigest()


class GoFileExtractor:
    """Extract direct download links from GoFile."""
    
    def __init__(self):
        self.session = Session()
        self.session.headers.update({
            "Accept-Encoding": "gzip",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Connection": "keep-alive",
            "Accept": "*/*",
            "Origin": "https://gofile.io",
            "Referer": "https://gofile.io/",
        })
        self._setup_account()
    
    def _setup_account(self):
        """Setup account token for authentication."""
        user_agent = str(self.session.headers.get("User-Agent", "Mozilla/5.0"))
        wt = generate_website_token(user_agent, "")
        
        try:
            response = self.session.post(
                "https://api.gofile.io/accounts",
                headers={"X-Website-Token": wt, "X-BL": "en-US"},
                timeout=15
            ).json()
            
            if response and response.get("status") == "ok":
                token = response['data']['token']
                self.session.cookies.set("Cookie", f"accountToken={token}")
                self.session.headers.update({"Authorization": f"Bearer {token}"})
        except Exception as e:
            print(f"Account setup failed: {e}")
    
    def format_size(self, size_bytes: int) -> str:
        """Format file size to human readable format."""
        if size_bytes == 0:
            return "0 B"
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024
            i += 1
        return f"{size_bytes:.2f} {size_names[i]}"
    
    def extract_links(self, url: str, password: Optional[str] = None) -> Dict[str, Any]:
        """Extract all direct download links from GoFile URL."""
        result = {
            "success": False,
            "files": [],
            "total_size": 0,
            "total_files": 0,
            "error": None,
            "url": url,
            "structure": None
        }
        
        try:
            # Validate URL
            if "/d/" not in url:
                result["error"] = "Invalid GoFile URL format. URL should contain '/d/'"
                return result
            
            # Extract content ID
            content_id = url.split("/")[-1]
            if not content_id or content_id == "d":
                result["error"] = "Could not extract content ID from URL"
                return result
            
            # Process with password if provided
            pwd_hash = sha256(password.encode()).hexdigest() if password else None
            content_data = self._fetch_content(content_id, pwd_hash)
            
            if content_data:
                files_data = self._extract_files(content_data)
                result["success"] = True
                result["files"] = files_data["files"]
                result["total_size"] = files_data["total_size"]
                result["total_files"] = files_data["total_files"]
                result["structure"] = files_data["structure"]
                
                # Format sizes for display
                for file in result["files"]:
                    file["size_formatted"] = self.format_size(file["size"])
                result["total_size_formatted"] = self.format_size(result["total_size"])
            else:
                result["error"] = "Failed to fetch content. The link may be invalid, expired, or password protected."
                
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    def _fetch_content(self, content_id: str, password: Optional[str] = None) -> Optional[Dict]:
        """Fetch content data from GoFile API."""
        url = f"https://api.gofile.io/contents/{content_id}?cache=true&sortField=createTime&sortDirection=1"
        
        if password:
            url = f"{url}&password={password}"
        
        user_agent = str(self.session.headers.get("User-Agent", "Mozilla/5.0"))
        auth_header = str(self.session.headers.get("Authorization", ""))
        account_token = auth_header.replace("Bearer ", "") if auth_header else ""
        wt = generate_website_token(user_agent, account_token)
        
        try:
            response = self.session.get(
                url=url,
                headers={"X-Website-Token": wt, "X-BL": "en-US"},
                timeout=30
            )
            json_response = response.json()
            
            if json_response and json_response.get("status") == "ok":
                return json_response.get("data")
            
        except Exception as e:
            print(f"Fetch error: {e}")
        
        return None
    
    def _extract_files(self, data: Dict, parent_path: str = "") -> Dict:
        """Extract files recursively from content data."""
        result = {
            "files": [],
            "total_size": 0,
            "total_files": 0,
            "structure": None
        }
        
        if data.get("type") == "folder":
            # Process folder
            folder_name = data.get("name", "root")
            folder_structure = {
                "name": folder_name,
                "type": "folder",
                "path": parent_path,
                "children": []
            }
            
            for child_id, child in data.get("children", {}).items():
                child_result = self._extract_files(child, 
                                                   os.path.join(parent_path, folder_name) if parent_path else folder_name)
                result["files"].extend(child_result["files"])
                result["total_size"] += child_result["total_size"]
                result["total_files"] += child_result["total_files"]
                if child_result.get("structure"):
                    folder_structure["children"].append(child_result["structure"])
            
            result["structure"] = folder_structure
        else:
            # Process file
            file_name = data.get("name", "file")
            file_size = int(data.get("size", 0))
            file_link = data.get("link")
            file_id = data.get("id")
            
            if file_link:
                file_info = {
                    "name": file_name,
                    "path": parent_path,
                    "full_path": os.path.join(parent_path, file_name) if parent_path else file_name,
                    "size": file_size,
                    "link": file_link,
                    "id": file_id,
                    "type": "file"
                }
                result["files"].append(file_info)
                result["total_size"] += file_size
                result["total_files"] += 1
                
                result["structure"] = {
                    "name": file_name,
                    "type": "file",
                    "path": parent_path,
                    "size": file_size,
                    "link": file_link,
                    "id": file_id
                }
        
        return result


# Initialize extractor
extractor = GoFileExtractor()


@app.route('/')
def index():
    """Render main page."""
    return render_template('index.html')


@app.route('/api/extract', methods=['POST'])
def extract():
    """API endpoint to extract download links."""
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({
            "success": False,
            "error": "URL is required"
        }), 400
    
    url = data['url'].strip()
    password = data.get('password')
    if password is not None and isinstance(password, str):
        password = password.strip() or None
    else:
        password = None
    
    # Validate URL
    if not url:
        return jsonify({
            "success": False,
            "error": "Please provide a GoFile URL"
        }), 400
    
    # Extract links
    result = extractor.extract_links(url, password)
    
    return jsonify(result)


@app.route('/api/validate', methods=['POST'])
def validate():
    """Validate GoFile URL."""
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({"valid": False, "error": "No URL provided"})
    
    # Simple URL validation
    pattern = r'https?://(www\.)?gofile\.io/d/[a-zA-Z0-9]+'
    valid = bool(re.match(pattern, url))
    
    if not valid:
        return jsonify({
            "valid": False,
            "error": "Invalid GoFile URL format. Example: https://gofile.io/d/abc123"
        })
    
    return jsonify({"valid": True})


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })


if __name__ == '__main__':
    import os
    app.run(debug=True, host='0.0.0.0', port=5000)
