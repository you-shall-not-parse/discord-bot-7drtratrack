import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
import time
import csv
import io

def extract_table_data(url="http://178.18.248.164:7012/games/5558"):
    # Configure Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    try:
        # Setup the driver
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        
        # Navigate to the page
        driver.get(url)
        print(f"Page title: {driver.title}")
        
        # Wait for table to load
        time.sleep(3)
        
        # Find the table
        table = driver.find_element(By.TAG_NAME, "table")
        
        # Extract headers
        headers = [th.text for th in table.find_elements(By.TAG_NAME, "th")]
        
        # Extract rows
        rows = []
        for tr in table.find_elements(By.TAG_NAME, "tr")[1:]:  # Skip header row
            row_data = [td.text for td in tr.find_elements(By.TAG_NAME, "td")]
            if row_data:  # Avoid empty rows
                rows.append(row_data)
        
        # Create DataFrame
        df = pd.DataFrame(rows, columns=headers)
        
        # Clean up
        driver.quit()
        
        return df
        
    except Exception as e:
        print(f"An error occurred: {e}")
        if 'driver' in locals():
            driver.quit()
        return None

# Try to extract via CSV URL first
def try_csv_extraction():
    try:
        CSV_URL = "http://178.18.248.164:7012/games/5558/export/csv"
        print(f"Attempting to download CSV directly from: {CSV_URL}")
        
        response = requests.get(CSV_URL)
        response.raise_for_status()
        
        # Read CSV directly from memory
        csv_data = list(csv.reader(io.StringIO(response.text)))
        
        # Convert to DataFrame
        headers = csv_data[0]
        data = csv_data[1:]
        return pd.DataFrame(data, columns=headers)
        
    except Exception as e:
        print(f"Direct CSV download failed: {e}")
        return None

# Main execution
if __name__ == "__main__":
    import requests
    
    # Try direct CSV download first
    df = try_csv_extraction()
    
    # If that fails, try with Selenium
    if df is None:
        print("Switching to Selenium extraction method...")
        df = extract_table_data()
    
    if df is not None:
        print("\nExtracted Data:")
        print(df.head())
        
        # Save to CSV
        df.to_csv("extracted_data.csv", index=False)
        print("Data saved to extracted_data.csv")
    else:
        print("Failed to extract data.")
