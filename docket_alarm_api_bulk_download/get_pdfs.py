# Built-in Modules
import os
import json
from multiprocessing import Pool, cpu_count
import re
import datetime
import threading
import time
import concurrent.futures
# Third-party Modules
from progress.bar import IncrementalBar
from tqdm import tqdm
import requests
import PyPDF2 #DEV
from retrying import retry
# Internal Modules
import config
import file_browser, global_variables
import gui #DEV
import log_errors_to_table
import login

CURRENT_DIR = os.path.dirname(__file__)

# Reinforces that the variables defined in the global_variables module, and then edited from within other modules,
# continue to have the value that the user changed it to.
# It may look redundant, but without this line, the script only uses the default variable, without reflecting changes.
global_variables.PDF_OUTPUT_PATH = global_variables.PDF_OUTPUT_PATH
global_variables.CLIENT_MATTER = global_variables.CLIENT_MATTER

# We create an instance of a lock object. We use this later when we download json with threads, to ensure that no 
# two threads try to access data in the same place at the same time, causing problems.
lock = threading.Lock()

# We create an ErrorTable object where we can write errors to an xlsx file as they come and then save the file at the end of the download.
tableErrorLog = log_errors_to_table.ErrorTable()

def cleanhtml(raw_html):
    """
    This function is for creating filenames from the HTML returned from the API call.
    It takes in a string containing HTML tags as an argument, and returns a string without HTML tags or any
    characters that can't be used in a filename.
    """

    # Created a regular expression pattern object for detecting HTML tags
    cleanr = re.compile('<.*?>')

    # Removes HTML tags from the string argument
    cleantext = re.sub(cleanr, '', raw_html)

    # Replaces spaces with underscores
    cleantext = str(cleantext).strip().replace(' ', '_')

    # Removes characters that can't be used in filenames
    cleantext = re.sub(r'(?u)[^-\w.]', '', cleantext)

    # Removes periods
    cleantext = cleantext.replace(".", "")

    # Cuts the name short so it doesn't go over the maximum amount of characters for a NTFS filename
    cleantext = cleantext[0:240]

    # Return the text free of html tags and symbols that can't be used in filenames.
    # It will also be cut to a length that doesn't go over the maximum length for a filename. 
    return cleantext

def get_urls(input_directory):
    """
    Takes in a directory full of JSON files as input, and returns the values for keys labeled 'link' for all of the files.
    The output is a list of tuples.
    The first item in each tuple is a string containing the link.
    The second item in each tuple is a string containing the name of the document the link is connected to.
    The third item in each tuple is a string containing the original file name of the json file that the link was retrieved from.
    """

    # This list will be appended throughout the function with every pdf link found within the json files.
    # It will ultimately be returned at the end of the function.
    pdf_list = []

    # The absolute path of the 'result' folder
    input_directory = global_variables.JSON_INPUT_OUTPUT_PATH

    # The client matter the user specified in the menus.
    CLIENT_MATTER = global_variables.CLIENT_MATTER

    if os.path.isdir(input_directory) == False:
        print("[ERROR] Could not write PDF files.\nMake sure 'json-output' folder exists in the root directroy of the program.\nCheck documentation for more information.\n")
        input()


    # Loops through every file in the 'result' directory.
    for file in os.listdir(input_directory):

        # We store our output path for PDFs from our global variable in a local variable.
        # The output path must be included in the result because when the resulting tuples get passed to our
        # download_from_link_list() function within the thread_download_pdfs() wrapper function, it can't access the 
        # global variable directly from inside the seperate threads.
        PDF_OUTPUT_PATH = global_variables.PDF_OUTPUT_PATH

        # Saves the name of each file in the folder to a variable
        filename = os.fsdecode(file)

        # Checks to ensure that we only run our code on .JSON files
        if not filename.lower().endswith(".json"):
            continue
        # Stores the absolute path of the current JSON file in the loop as a variable. 
        path = os.path.join(input_directory, filename)

        # Designates the extenstion '.json' as a regex pattern that we want to remove from a string.
        text_to_remove = re.compile(re.escape('.json'), re.IGNORECASE)

        # Uses the regex above to remove '.json' from the json filenames, which we will use to name the folders that
        # will contain the corresponding pdfs to the original json file.
        base_filename = text_to_remove.sub("", filename)
        
        # Opens each individual JSON file
        with open(path) as jsonFile:

            # Allows us to work with JSON files the same way we would work with a Python dictionary.
            jsonObject = json.load(jsonFile)
            
            # Checks to see if a 'docket_report' key exists in the current JSON file in the loop.
            if "docket_report" in jsonObject:

                # If it exists, it saves the value for the docket_report key in a variable.
                docket_report = jsonObject['docket_report']

                # docket_report will be a list of dictionaries. This loops through each dictionary in the list.
                for item in docket_report:
                    
                    docName = item['contents']

                    # We run the cleanhtml() function on the document name to remove the HTML tags and acharcters that can't be used in filenames
                    docName = cleanhtml(docName)

                    # The ID number of the document. We use this later for the file names
                    docNum = item['number']

                    # Checks to see if any of the dictionaries inside the list contain a 'link' key
                    if 'link' in item:

                        # The 'link' key contains a link to a PDF file associated with that item in the docket report.
                        link = item['link']

                        link_filename = f"{docNum} - {docName}"

                        link_tuple = (link, link_filename, base_filename, PDF_OUTPUT_PATH, CLIENT_MATTER)
                        # Add the found link to the list, which will ultimately be returned at the end of the function.
                        pdf_list.append(link_tuple)

                    # Some PDF's are inside the exhibits key, which doesnt always exist. Here, we check to see if the exhibits key exists.
                    if 'exhibits' in item:

                        # if it does exist, we save its contents in an exhibits variable.
                        exhibits = item['exhibits']
                        
                        # The data contained inside 'exhibits' will be a list of dictionaries. So we loop through the list to access the data.
                        for exhibit in exhibits:

                            # We chck to see if any links exist inside exhibits
                            if 'link' in exhibit:

                                exhibitNumber = f"{exhibit['exhibit']}"

                                # If a link to a PDF does exist, we store it in a variable.
                                exhibitLink = exhibit['link']
                                
                                # We create a file name to save the exhibit pdf as
                                exhibitName = f"Exhibit {exhibitNumber} - {docNum} - {docName}"

                                # We package the name, link, and filename together in a tuple, that will be passed as an argument to our
                                # download_from_link_list() function within the thread_download_pdfs() function where we use map to
                                # downloading with seperate threads, speeding things up.
                                exhibitLink_tuple = (exhibitLink, exhibitName, base_filename, PDF_OUTPUT_PATH, CLIENT_MATTER)
                                pdf_list.append(exhibitLink_tuple)

            # We close the file when we are done. This also ensures that the file is saved.    
            jsonFile.close()
    return pdf_list

@retry
def download_from_link_list(link_list):
    """
    Downloads PDF documents from the web and saves them in a specified folder.
    Takes in 3 string arguments:
    1. Link to a pdf file
    2. File name we will save as
    3. Name of the folder we will create to store our PDFs.
    Notice how the arguments are the same as what the get_urls() function returns.
    This function Isn't made to be used on its own, but can be.
    """

    # We store a user object we can use to login
    user = login.Credentials()

    # We unpack the tuple, assigning each value to a human-readable variable name.
    link, fileName, folderName, outputPath, CLIENT_MATTER = link_list

    # The directory where we will create the subdirectories within for each individual docket
    outputDirectoryPath = os.path.join(outputPath, folderName)
    # The path we are saving the file to, inside the subdirectory we will create.
    outputFilePath = os.path.join(outputDirectoryPath, f"{fileName}.pdf")
    
    # We open a lock so threads can't run this block of code simultaneously since that would cause errors
    with lock: 
        # If the directory for the docket doesn't yet exist...
        if not os.path.exists(outputDirectoryPath):

            # Then, create it!
            os.makedirs(outputDirectoryPath)
    
    
    # We ready our authentication token to pass as a paramater with our http request to get the pdf file. You must be logged in to access the files.
    params = {
            "login_token": user.authenticate(),
            "client_matter": CLIENT_MATTER,
            }

    # We then make an http request to the pdf link and save the result in a variable. We pass the authentication token as a parameter.
    result = requests.get(link, stream=True, params=params)

    try:
        # If the http request failed, we have it throw a detailed error message. This is not immediately shown to the user and we let the donwload
        # continue for now.
        result.raise_for_status()
    
    except Exception as a:
        timeNow = datetime.datetime.now().strftime("%I:%M%p %B %d, %Y")
        with lock:
            # We write the error to log/log.txt with a timestamp and detailed information about which case caused the error.
            with open(os.path.join(CURRENT_DIR, 'log', 'log.txt'), 'a') as errorlog:
                errorlog.write(f"\n{timeNow}\n")
                errorlog.write(f"{a}")
                errorlog.write(f"\n{link}\n{fileName}\n{folderName}\n{outputPath}\n------------------")
        
            # We write the error to a csv file that will be stored in the log folder when the download finishes.
            tableErrorLog.append_error_table(f"{a}", folderName, fileName)
            return


    try:
        # Once the folder is created, we can create a file inside it, open it, and...
        with open(outputFilePath, "wb") as e:

            # Write the contents of the PDF to the place we specify.
            e.write(result.content)
    
    except Exception as a:
        print(a)
    
    return


def thread_download_pdfs(link_list):
    """
    Wrapper of download_from_link_list()
    Takes in a link_list generated by the get_urls() function.
    """

    # Gets the amount of links that will be downloaded. We use this later because the progress bar takes the maximum
    # amount of downloads as a parameter
    maximum = len(link_list)

    print("Downloading PDF files...")

    # Starts a timer, we end the timer after we run the function with threading to see how long the bulk download
    # took in total.
    start = time.perf_counter()
    # We start up the threading executor
    with concurrent.futures.ThreadPoolExecutor() as executor:
        try:
            # We use executor.map() to select our function and the arguments that will be passed to it in each new thread.
            # We wrap this in list(tdqm()) to add the progress bar. See stackoverflow page below for more info.
            # https://stackoverflow.com/questions/51601756/use-tqdm-with-concurrent-futures
            results = list(tqdm(executor.map(download_from_link_list, link_list), total=maximum))
        except FileExistsError as fee:
            # If we get a FileExistsError, we let the user know that the directory they save to must be empty.
            print("[ERROR] Directory you're saving PDFs to must be empty.")
            input()
            # After pressig enter, we print the error thrown out to the user.
            print(fee)
    # We finish our timer.
    finish = time.perf_counter()
    # We display the amount of time the downloads took all together.
    print(f"Finished downloading PDF files in {round(finish - start)} seconds.")
    # We save the current date and time in a variable
    currentDateTime = datetime.datetime.now().strftime("%I%M%p %B %d, %Y")
    # We save our csv log that has been tracking any errors throughout the downloads.
    # If any PDFs will not open, then they wil be displayed in this PDF.
    # The file will be in the log folder and will be named according to the date and time when
    # the download finished.
    tableErrorLog.error_excel_save(os.path.join(CURRENT_DIR, "log", f"logTable - {currentDateTime}.xlsx"))
    # We must return results to make the progress bar work.
    try:
        os.startfile(global_variables.PDF_OUTPUT_PATH)
    except:
        pass
        
    return results








        
        
