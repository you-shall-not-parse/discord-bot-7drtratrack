import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time
import io
import csv

def extract_table_data():
    # Set up Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in headless mode (no GUI)
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # Initialize the Chrome webdriver
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    try:
        # Navigate to the page containing the table
        driver.get("http://178.18.248.164:7012/games/5558")  # Adjust the URL to your actual page
        print("Page loaded")
        
        # Wait for the table to load
        time.sleep(3)  # You might need to adjust this delay
        
        # Option 1: If there's a "Download CSV" button, click it
        try:
            # Try to find and click a download button (adjust the selector as needed)
            csv_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Export CSV')]")
            csv_button.click()
            print("CSV export button clicked")
            time.sleep(2)  # Wait for download to initialize
            # Note: This approach will download to your downloads folder
            # You'll need additional code to process the downloaded file
        except Exception as e:
            print(f"Could not find CSV export button: {e}")
        
        # Option 2: Extract data directly from the table
        table_data = []
        try:
            # Find the table element (adjust the selector as needed)
            table = driver.find_element(By.TAG_NAME, "table")
            
            # Get all rows from the table
            rows = table.find_elements(By.TAG_NAME, "tr")
            
            # Extract headers
            header_row = rows[0]
            headers = [header.text for header in header_row.find_elements(By.TAG_NAME, "th")]
            table_data.append(headers)
            
            # Extract data rows
            for row in rows[1:]:
                cells = row.find_elements(By.TAG_NAME, "td")
                row_data = [cell.text for cell in cells]
                table_data.append(row_data)
                
            print(f"Extracted {len(table_data)-1} rows of data")
            
            # Convert to DataFrame for easier handling
            df = pd.DataFrame(table_data[1:], columns=table_data[0])
            print(df.head())
            
            return df
            
        except Exception as e:
            print(f"Error extracting table data: {e}")
            
    finally:
        # Close the browser
        driver.quit()

if __name__ == "__main__":
    data = extract_table_data()
    
    # Optional: Save to CSV
    if data is not None:
        data.to_csv('extracted_data.csv', index=False)
        print("Data saved to extracted_data.csv")
