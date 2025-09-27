import requests
import csv
import io
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import discord
from discord.ext import commands

class StatExtractor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.CSV_URL = "http://178.18.248.164:7012/games/5558/export/csv"
    
    @commands.command()
    async def extract_stats(self, ctx):
        """Extract statistics from the game website"""
        await ctx.send("Extracting statistics, please wait...")
        
        try:
            # First try direct CSV download approach
            response = requests.get(self.CSV_URL)
            if response.status_code == 200 and 'text/csv' in response.headers.get('Content-Type', ''):
                # Process the CSV data
                stats = self._process_csv(response.text)
                await ctx.send("Successfully extracted stats!")
                return stats
            
            # If direct download failed, try Selenium approach
            await ctx.send("Direct download failed. Trying browser automation...")
            stats = self._use_selenium_extraction()
            await ctx.send("Successfully extracted stats via browser automation!")
            return stats
            
        except Exception as e:
            await ctx.send(f"Error extracting stats: {str(e)}")
            return None
    
    def _process_csv(self, csv_text):
        # Read CSV directly from memory
        csv_data = csv.reader(io.StringIO(csv_text))
        
        # Skip header
        headers = next(csv_data)
        print("Headers:", headers)
        
        # Convert to list of dictionaries for easier processing
        results = []
        for row in csv_data:
            if len(row) == len(headers):
                results.append(dict(zip(headers, row)))
        
        return results
    
    def _use_selenium_extraction(self):
        # Setup headless browser
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        
        with webdriver.Chrome(options=options) as driver:
            # Navigate to the game stats page - adjust URL as needed
            base_url = "http://178.18.248.164:7012/games/5558"
            driver.get(base_url)
            
            # Wait for the page to load
            time.sleep(2)
            
            # Look for an export button or link and click it
            try:
                export_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Export')] | //a[contains(text(), 'CSV')]"))
                )
                export_button.click()
                time.sleep(2)  # Wait for download to initialize
            except:
                print("Could not find export button, trying to extract from table")
            
            # If clicking an export button doesn't work, try to scrape the table directly
            tables = driver.find_elements(By.TAG_NAME, "table")
            if tables:
                results = []
                table = tables[0]  # Assuming the first table is the stats table
                
                # Get headers
                headers = [th.text for th in table.find_elements(By.TAG_NAME, "th")]
                
                # Get rows
                rows = table.find_elements(By.TAG_NAME, "tr")[1:]  # Skip header row
                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) == len(headers):
                        results.append(dict(zip(headers, [cell.text for cell in cells])))
                
                return results
            
            return None

def setup(bot):
    bot.add_cog(StatExtractor(bot))
