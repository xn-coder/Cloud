from fastapi import FastAPI, UploadFile, HTTPException, Form, File
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from telethon import TelegramClient, errors
from telethon.types import Message
import logging
import io
import datetime
import base64
from fastapi.requests import Request
from telethon.sessions import StringSession
from fastapi.templating import Jinja2Templates
import asyncio
from cryptography.fernet import Fernet
import zipfile
from typing import List

api_id = 21020320
api_hash = '4d7eae02be994fb80da9db278f189850'
key = 'V-mbvTB1fAM9EP-gPN0BhwvMZbJyFMsB_Mv9BFJFupk='

app = FastAPI()

templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
fernet = Fernet(key)

def encrypt_session_string(session_string):
    encrypted_session_string = fernet.encrypt(session_string.encode()).decode()
    return encrypted_session_string

def decrypt_session_string(encrypted_session_string):
    decrypted_session_string = fernet.decrypt(encrypted_session_string.encode()).decode()
    return decrypted_session_string

def get_client(session_string=None, reset=False):
    # Use local loop
    loop = asyncio.get_event_loop()
    client = TelegramClient(StringSession(session_string), api_id, api_hash, loop=loop)
    return client

progress_updates = []
def progress_callback(current, total, file_name):
    print(f"progress : {current} out of {total}")
    progress = [file_name, current, total]
    progress_updates.append(progress)

@app.get("/sse/progress/")
async def sse_progress():
    async def event_generator():
        while True:
            if progress_updates:
                progress = progress_updates.pop(0)
                yield f"data: {progress}\n\n"
            await asyncio.sleep(1)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login")
async def login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/verify")
async def verify(request: Request):
    return templates.TemplateResponse("verify.html", {"request": request})

@app.post("/sign-in/")
async def sign_in(phone_number: str = Form(...)):
    try:
        client = get_client()
        await client.connect()
        if not await client.is_user_authorized():
            phone_code = await client.send_code_request(phone_number)
            await client.disconnect()
            return { "status": "success", "message": "Code sent to your phone number", "phone_code": phone_code.phone_code_hash}
        await client.disconnect()
        return { "status": "success", "message": "Already signed in", "session_string": client.session.save()}
    except errors.FloodWaitError as e:
        raise HTTPException(status_code=429, detail=f"Too many requests. Please wait for {e.seconds} seconds.")
    except errors.PhoneNumberFloodError:
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
    except errors.PhoneNumberBannedError:
        raise HTTPException(status_code=403, detail="This phone number is banned.")
    except errors.PhoneNumberInvalidError:
        raise HTTPException(status_code=400, detail="The phone number is invalid.")
    except Exception as e:
        logger.error(f"Error during sign-in: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/verify-code/")
async def verify_code(phone_number: str = Form(...), code: str = Form(...), phone_code_hash: str = Form(...), password: str = Form(None), session_string: str = Form(None)):
    try:
        if session_string is not None:
            client = get_client(decrypt_session_string(session_string))
        else:
            client = get_client()
        await client.connect()
        if not await client.is_user_authorized():
            try:
                await client.sign_in(phone_number, code, phone_code_hash=phone_code_hash)
            except errors.SessionPasswordNeededError:
                if password is None:
                    await client.disconnect()
                    return { "status": "error", "message": "2FA enabled"}
                await client.sign_in(password=password)
            session_string = client.session.save()
            await client.disconnect()
            return { "status": "success", "message": "Signed in successfully", "session_string": encrypt_session_string(session_string)}
        await client.disconnect()
        return { "status": "success", "message": "Already signed in", "session_string": encrypt_session_string(client.session.save())}
    except Exception as e:
        logger.error(f"Error during code verification: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/logout/")
async def logout(session_string: str = Form(None)):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out()
            await client.disconnect()
            get_client(reset=True)
            return { "status": "success", "message": "Logout successfully"}
        await client.disconnect()
        get_client(reset=True)
        return { "status": "success", "message": "Already signed in"}
    except Exception as e:
        logger.error(f"Error during code verification: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload/")
async def upload_files(file: UploadFile = File(...), session_string: str = Form(...)):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()
        
        media_files = None
        
        file_bytes = await file.read()
        file_stream = io.BytesIO(file_bytes)
        file_stream.name = file.filename
        fileType = file.headers['content-type'].split("/")[1]
        msg = "XNCODER-P-"+fileType.upper()+"-"+str(datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    
        message = await client.send_file(
            'me', 
            file_stream, 
            progress_callback=lambda current, total: progress_callback(current, total, file.filename), 
            part_size_kb=512,  # Reduced part size for faster uploads
            parallel=32,       # Increased parallel connections
            caption=msg
        )

        if fileType in ['jpeg', 'jpg', 'png']:
            media_files = {
                'type': "image",
                'id': message.id,
                'message': message.message
            }
        elif fileType in ['mp4', 'mov', 'vid']:
            media_files = {
                'type': "video",
                'id': message.id,
                'message': message.message
            }
                
        await client.disconnect()
        return JSONResponse({'status': 'success', 'file': media_files})
    except Exception as e:
        logger.error(f"Error during file upload: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{id}")
async def download_file(id: str, session_string: str, request: Request):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()
        message = await client.get_messages('me', ids=int(id))
        if not message.media:
            raise HTTPException(status_code=404, detail="File not found")

        file_size = message.file.size

        range_header = request.headers.get('Range')
        if range_header:
            range_start, range_end = range_header.replace('bytes=', '').split('-')
            range_start = int(range_start)
            range_end = int(range_end) if range_end else file_size - 1
            length = range_end - range_start + 1

            async def iterfile():
                async for chunk in client.iter_download(message.media, offset=range_start, limit=length):
                    yield bytes(chunk)

            headers = {
                'Content-Range': f'bytes {range_start}-{range_end}/{file_size}',
                'Accept-Ranges': 'bytes',
                'Content-Length': str(length),
                'Content-Disposition': f'attachment; filename={message.file.name}'
            }
            return StreamingResponse(iterfile(), status_code=206, headers=headers)
        else:
            async def iterfile():
                async for chunk in client.iter_download(message.media):
                    yield bytes(chunk)

            headers = {
                'Content-Length': str(file_size),
                'Content-Disposition': f'attachment; filename={message.file.name}'
            }
            return StreamingResponse(iterfile(), headers=headers)
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/remove/")
async def remove(id: str, session_string:str):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()
        await client.delete_messages('me', message_ids=int(id))
        await client.disconnect()
        return {"message": "File removed successfully"}
    except Exception as e:
        logger.error(f"Error removing file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/media/")
async def media(id: str, type: str, session_string: str):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()
        message = await client.get_messages('me', ids=int(id))
        if not message.media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        buffer = io.BytesIO()
        
        if type == "image":
            await message.download_media(file=buffer)
            buffer.seek(0)
            base64_media = base64.b64encode(buffer.read()).decode('utf-8')
            media_data = {
                'type': 'image',
                'src': f'data:image/jpeg;base64,{base64_media}',
                'message': message.message
            }
            await client.disconnect()
            return JSONResponse({'status': 'success', **media_data})
        elif type == "video":
            if "video/mp4" in message.media.document.mime_type and message.media.document.thumbs:
                thumb = message.media.document.thumbs[-1]
                await message.download_media(thumb=thumb, file=buffer)
                buffer.seek(0)
                base64_thumb = base64.b64encode(buffer.read()).decode('utf-8')
                duration_seconds = message.media.document.attributes[0].duration
                minutes, seconds = divmod(int(duration_seconds), 60)
                duration_formatted = f"{minutes:02}:{seconds:02}"
                media_data = {
                    'type': 'video',
                    'src': f"data:image/jpeg;base64,{base64_thumb}",
                    'duration': duration_formatted,
                    'message': message.message
                }
                await client.disconnect()
                return JSONResponse({'status': 'success', **media_data})
    except Exception as e:
        logger.error(f"Error serving media: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/stream/{id}")
async def stream_video(id: str, session_string: str, request: Request):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()
        message = await client.get_messages('me', ids=int(id))
        if not message.media:
            raise HTTPException(status_code=404, detail="File not found")

        file_size = message.file.size

        range_header = request.headers.get('Range')
        if range_header:
            range_start, range_end = range_header.replace('bytes=', '').split('-')
            range_start = int(range_start)
            range_end = int(range_end) if range_end else file_size - 1
            length = range_end - range_start + 1

            async def iterfile():
                async for chunk in client.iter_download(message.media, offset=range_start, limit=length):
                    yield bytes(chunk)

            headers = {
                'Content-Range': f'bytes {range_start}-{range_end}/{file_size}',
                'Accept-Ranges': 'bytes',
                'Content-Length': str(length),
                'Content-Type': 'video/mp4'
            }
            return StreamingResponse(iterfile(), status_code=206, headers=headers)
        else:
            async def iterfile():
                async for chunk in client.iter_download(message.media):
                    yield bytes(chunk)

            headers = {
                'Content-Length': str(file_size),
                'Content-Type': 'video/mp4'
            }
            return StreamingResponse(iterfile(), headers=headers)
    except Exception as e:
        logger.error(f"Error streaming video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/list-files/")
async def list_files(session_string: str, offset: int = 0):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()
        messages = await client.get_messages('me', offset_id=offset, limit=20)
        media_files = []
        offset_id = None
        for message in messages:
            if isinstance(message, Message):
                md = False
                message_id = None
                if message.message.startswith("XNCODER") and message.message[10:message.message.find("-", 10+1)] in ["JPEG", "JPG", "PNG"] and message.media:
                    md = True
                    type = "image"
                    message_id = message.id
                    msg = message.message
                elif message.message.startswith("XNCODER") and message.message[10:message.message.find("-", 10+1)] in ["VID", "MOV", "MP4"]  and message.media:
                    md = True
                    type = "video"
                    message_id = message.id
                    msg = message.message
                offset_id = message_id
                
                if md and message_id is not None:  # Check if message_id is set
                    media_file = {
                        'type': type,
                        'id': message_id,
                        'message': msg
                    }
                    media_files.append(media_file)
        await client.disconnect()
        return {"files": media_files, "offset_id": offset_id}
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/download-all/")
async def download_all(ids: List[str] = Form(...), session_string: str = Form(...)):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()

        zip_stream = io.BytesIO()
        with zipfile.ZipFile(zip_stream, "w", zipfile.ZIP_DEFLATED) as zip_file:
            async def download_and_write(id):
                message = await client.get_messages('me', ids=int(id))
                file_name = message.message[message.message.find("-", 11)+1:] + "." + message.message[10:message.message.find("-", 10+1)]
                if not message.media:
                    raise HTTPException(status_code=404, detail="File not found")
                
                buffer = io.BytesIO()
                async for chunk in client.iter_download(message.media):
                    buffer.write(chunk)
                buffer.seek(0)
                zip_file.writestr(file_name, buffer.read())

            await asyncio.gather(*(download_and_write(id) for id in ids))  # Download files concurrently

        await client.disconnect()

        zip_stream.seek(0)

        headers = {
            'Content-Disposition': f'attachment; filename="XNCODER-{datetime.datetime.now().strftime("%Y%m%d%H%M%S")}.zip"'
        }
        return StreamingResponse(zip_stream, media_type="application/zip", headers=headers)
    except Exception as e:
        logger.error(f"Error downloading files: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/remove-all/")
async def remove_all(ids: List[str] = Form(...), session_string: str = Form(...)):
    try:
        client = get_client(decrypt_session_string(session_string))
        await client.connect()

        for id in ids:
            await client.delete_messages('me', message_ids=int(id))

        await client.disconnect()
        return {"status": "success", "message": "Files removed successfully"}
    except Exception as e:
        logger.error(f"Error removing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)