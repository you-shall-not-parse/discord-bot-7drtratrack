import requests

URL = "http://178.18.248.164:7012/games/5558"

response = requests.get(URL)

# Print first 1000 characters of raw HTML
print(response.text[:1000])

