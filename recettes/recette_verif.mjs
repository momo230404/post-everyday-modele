import { chromium } from 'playwright';
const B='https://votre-domaine.fr';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1280,height:1200} });
const err=[]; p.on('pageerror',e=>err.push(String(e)));
let ok=0; const v=(c,q)=>{ if(!c) throw new Error('✗ '+q); ok++; console.log('  ✓',q); };
await p.goto(B+'/connexion',{waitUntil:'networkidle'});
// ⚠️ Jamais d'identifiants en clair ici : ce fichier part dans le dépôt.
//    Lancer la recette ainsi :
//    PE_IDENT=moi PE_MDP='…' node recettes/recette_verif.mjs
const ID = process.env.PE_IDENT, MDP = process.env.PE_MDP;
if (!ID || !MDP) { console.error('Renseignez PE_IDENT et PE_MDP.'); process.exit(1); }
await p.fill('input[name=identifiant]', ID); await p.fill('input[name=motdepasse]', MDP);
await p.click('form.boite button'); await p.waitForTimeout(3000);
await p.evaluate(()=>sessionStorage.setItem('pe_entre','1'));
await p.reload({waitUntil:'networkidle'}); await p.waitForTimeout(3000);
await p.evaluate(()=>ouvrirConnexions()); await p.waitForTimeout(2500);

const tik = p.locator('.res-carte').filter({ has: p.locator('.role', { hasText: /^TikTok$/i }) }).first();
const t = await tik.evaluate(e=>e.innerHTML);
v(/URL properties/.test(t), 'la carte TikTok explique la vérification « URL properties »');
v(await tik.locator('#vf-nom-tiktok').count()===1, 'le champ du nom de fichier est là');
v(t.includes('verif-liste')||t.includes('Aucun fichier'), 'la liste des fichiers posés est affichée');

// Poser puis retirer un fichier, par le même chemin que le bouton de l'écran.
// (Le portail « Pour qui publie-t-on ? » recouvre la page : on appelle l'API
//  directement plutôt que de lutter contre un voile.)
const pose = await p.evaluate(async () => (await fetch('/api/verifications', {method:'POST',
  headers:{'content-type':'application/json'},
  body: JSON.stringify({nom:'tiktokZZecran999.txt', contenu:'tiktok-developers-site-verification=ZZECRAN'})})).json());
v(pose.ok, 'un fichier se pose depuis l’application');
const dehors = await p.evaluate(async () => {
  const r = await fetch('/tiktokZZecran999.txt', {redirect:'manual'});
  return { s:r.status, txt:(await r.text()).trim() };
});
v(dehors.s===200 && dehors.txt.includes('ZZECRAN'), 'et TikTok pourra le lire sans connexion : ' + dehors.txt);
const mauvais = await p.evaluate(async () => (await fetch('/api/verifications', {method:'POST',
  headers:{'content-type':'application/json'},
  body: JSON.stringify({nom:'../../etc/passwd', contenu:'x'})})).json());
v(!!mauvais.erreur, 'un nom de fichier hors format est refusé');
// Ménage
await p.evaluate(async () => {
  for (const n of ['tiktokZZecran999.txt','tiktokZZrecette1234.txt'])
    await fetch('/api/verifications/'+n, {method:'DELETE'});
});
const reste = await p.evaluate(async () => (await fetch('/api/verifications')).json());
v((reste.fichiers||[]).length === 0, 'fichiers de recette retirés, installation rendue intacte');

console.log('\nerreurs JS :', err.length?err.slice(0,2):'aucune');
console.log(ok+' vérifications ✓');
await b.close();
