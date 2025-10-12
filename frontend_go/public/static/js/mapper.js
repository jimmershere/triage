const segs = document.querySelectorAll('#segments li');
const canvas = document.getElementById('canvas');

segs.forEach(li => {
  li.addEventListener('dragstart', e => {
    e.dataTransfer.setData('text/plain', li.textContent);
  });
});

canvas.addEventListener('dragover', e => { e.preventDefault(); });
canvas.addEventListener('drop', e => {
  e.preventDefault();
  const t = e.dataTransfer.getData('text/plain');
  const chip = document.createElement('span');
  chip.className = 'badge';
  chip.textContent = t;
  chip.style.marginRight = '6px';
  canvas.appendChild(chip);
});
