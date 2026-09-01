import requests
from bs4 import BeautifulSoup

url = 'https://www.wbuscatti.com.br/categoria-produto/pulseiras/couro/'
html = requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=30).text
soup = BeautifulSoup(html, 'lxml')
card = soup.select_one('.product-card')
if not card:
    print('no card')
else:
    print('name:', card.select_one('.card-product-name').get_text(' ', strip=True) if card.select_one('.card-product-name') else None)
    p = card.select_one('.product-card-main-price, .product-card-price-new, .product-prices')
    print('price text:', p.get_text(' ', strip=True) if p else None)
    a = card.select_one('a[href]')
    print('href:', a['href'] if a else None)
    print('classes:', card.get('class'))
    print('snippet:', str(card)[:1200])
