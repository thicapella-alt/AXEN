import requests
from bs4 import BeautifulSoup

url = 'https://www.wbuscatti.com.br/categoria-produto/pulseiras/couro/'
html = requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=30).text
soup = BeautifulSoup(html, 'lxml')
name_el = soup.select_one('.card-product-name')
print('has name:', bool(name_el))
if name_el:
    print('name text:', name_el.get_text(' ', strip=True))
    parent = name_el
    for i in range(4):
        parent = parent.parent
        if not parent: break
        print('level', i+1, 'tag', parent.name, 'class', parent.get('class'))
    print('parent snippet:', str(name_el.parent)[:1000])
price_el = soup.select_one('.product-card-main-price, .product-card-price-new')
print('has price:', bool(price_el), 'text:', price_el.get_text(' ', strip=True) if price_el else None)
