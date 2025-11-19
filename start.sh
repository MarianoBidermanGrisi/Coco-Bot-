#!/bin/bash
=======
#!/bin/bash
>>>>>>> b64ec1fe6d27bd204ea69bffac4791dda7ce2a9d
gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 bot_web_service:app