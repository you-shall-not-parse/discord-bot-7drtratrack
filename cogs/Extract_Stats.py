import requests
import csv
import io

# Replace with the actual CSV link you copied from the site
CSV_URL = "http://178.18.248.164:7012/games/5558/export/csv"

response = requests.get(CSV_URL)
response.raise_for_status()

# Read CSV directly from memory
csv_data = csv.reader(io.StringIO(response.text))

# Skip header
headers = next(csv_data)
print("Headers:", headers)

# Example: print each player row
for row in csv_data:
    print(row)
