
document.querySelectorAll('[data-menu]').forEach(b=>b.addEventListener('click',()=>document.body.classList.toggle('nav-open')));
document.addEventListener('click',e=>{if(document.body.classList.contains('nav-open')&&!e.target.closest('.sidebar')&&!e.target.closest('[data-menu]'))document.body.classList.remove('nav-open')});
const io='IntersectionObserver'in window?new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('show');io.unobserve(e.target)}}),{threshold:.08}):null;
document.querySelectorAll('.reveal').forEach(el=>io?io.observe(el):el.classList.add('show'));

(function(){
  const path = location.pathname;
  const $ = (s, root=document) => root.querySelector(s);
  const $$ = (s, root=document) => [...root.querySelectorAll(s)];
  const json = async (url, options={}) => {
    const r = await fetch(url, {credentials:"same-origin", ...options});
    const raw = await r.text();
    let d = {};
    if (raw) {
      try { d = JSON.parse(raw); }
      catch {
        throw Object.assign(
          new Error(r.ok ? "The server returned an invalid response." : "This deployment is not connected to the WebLoom backend."),
          {status:r.status, data:{raw:raw.slice(0,180)}}
        );
      }
    }
    if (!r.ok) throw Object.assign(new Error(d.error || "Request failed."), {status:r.status, data:d});
    return d;
  };
  const apiPost = (url, body={}) => json(url, {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)
  });
  const apiPatch = (url, body={}) => json(url, {
    method:"PATCH",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)
  });
  const fmtBytes = n => {
    const v = Number(n||0);
    if (v < 1024) return v+" B";
    if (v < 1048576) return (v/1024).toFixed(1)+" KB";
    if (v < 1073741824) return (v/1048576).toFixed(1)+" MB";
    return (v/1073741824).toFixed(1)+" GB";
  };
  const hostOf = url => { try { return new URL(url).hostname; } catch { return url; } };
  const goSignin = () => { location.href = "/signin?next=" + encodeURIComponent(path + location.search); };

  async function hydrateUser(){
    try{
      const d = await json("/api/me");
      if(!d.authenticated){
        if(/^\/(dashboard|project|billing|account|settings|admin)/.test(path)) goSignin();
        return null;
      }
      const u=d.user||{};
      $$(".plan-chip b").forEach(el=>el.textContent=u.role==="owner"?"Owner access":u.plan==="pro"?"Pro plan":"Free plan");
      $$(".plan-chip p").forEach(el=>el.textContent=u.role==="owner"?"Unlimited captures + private tools.":u.plan==="pro"?"Unlimited captures enabled.":u.free_capture_used?"Free capture used.":"One complete capture included.");
      $$(".avatar").forEach(el=>{
        const email=(u.email||"WL").trim();
        el.textContent=(email.slice(0,2)||"WL").toUpperCase();
      });
      if(u.role==="owner" && !$('.side-nav a[href="/admin"]')){
        $(".side-nav").slice(-1)[0]?.insertAdjacentHTML("beforeend", '<a href="/admin"><svg viewBox="0 0 24 24"><path d="M4 5h16v14H4zM8 9h8M8 13h5"/></svg>Owner console</a>');
      }
      return u;
    }catch(e){ return null; }
  }

  function authPage(mode){
    const form=$(".auth-form");
    if(!form) return;
    const button=$("button",form);
    const email=$('input[type="email"]',form);
    const secret=$('input[type="password"]',form);
    if(!button||!email||!secret) return;
    let note=document.createElement("p");
    note.className="auth-error";
    note.setAttribute("role","alert");
    button.insertAdjacentElement("afterend",note);
    const run=async()=>{
      note.textContent="";
      const label=button.textContent;
      button.disabled=true;
      button.textContent=mode==="signin"?"Signing in…":"Creating account…";
      try{
        const d=await apiPost("/api/auth/"+mode,{email:email.value.trim(),password:secret.value});
        if(d.confirmation_required){
          note.classList.add("ok");
          note.textContent="Check your email to confirm your account.";
          button.textContent="Email sent";
          return;
        }
        const next=new URLSearchParams(location.search).get("next");
        location.href=next||d.redirect||"/dashboard";
      }catch(e){
        note.classList.remove("ok");
        note.textContent=e.message;
        button.disabled=false;
        button.textContent=label;
      }
    };
    button.addEventListener("click",run);
    form.addEventListener("submit",e=>{e.preventDefault();run();});
  }

  async function dashboard(){
    if(path!=="/dashboard") return;
    const u=await hydrateUser();
    if(!u) return;
    try{
      const d=await json("/api/projects");
      const projects=d.projects||[];
      const metrics=$$(".metric-value");
      if(metrics[0]) metrics[0].textContent=u.role==="owner"?"Owner":u.plan==="pro"?"Pro":"Free";
      if(metrics[1]) metrics[1].textContent=String(projects.filter(p=>p.status==="done").length);
      if(metrics[2]) metrics[2].textContent=fmtBytes(projects.reduce((n,p)=>n+Number(p.byte_count||0),0));
      const empty=$(".card.empty");
      const head=$(".section-head");
      if(projects.length && empty){
        empty.classList.remove("empty");
        empty.innerHTML='<div class="project-list"></div>';
        const list=$(".project-list",empty);
        projects.slice(0,8).forEach(p=>{
          const row=document.createElement("a");
          row.className="project-row";
          row.href="/project/"+p.id;
          row.innerHTML='<div class="project-favicon">'+hostOf(p.source_url).slice(0,1).toUpperCase()+'</div><div class="project-row-main"><b>'+hostOf(p.source_url)+'</b><span>'+p.source_url+'</span></div><span class="status-pill '+p.status+'">'+p.status+'</span><span class="project-meta">'+(p.file_count||0)+' files · '+fmtBytes(p.byte_count)+'</span><span class="row-arrow">→</span>';
          list.appendChild(row);
        });
        if(head) $("p",head).textContent=projects.length+" saved project"+(projects.length===1?"":"s");
      }
    }catch(e){}
  }

  async function projectPage(){
    if(!path.startsWith("/project")) return;
    const u=await hydrateUser();
    if(!u) return;
    const id=path.split("/")[2];
    const panel=$("#projectPanel")||$(".preview-box");
    if(!id){
      if(!panel) return;
      try{
        const d=await json("/api/projects");
        const projects=d.projects||[];
        const h=$(".hero-row h1");
        if(h) h.textContent="Projects";
        const desc=$(".hero-row p");
        if(desc) desc.textContent="Every capture, preview and export in one place.";
        const tabs=$("[data-project-tab]");
        tabs.forEach(a=>a.style.display="none");
        if(!projects.length){
          panel.innerHTML='<div class="project-empty-copy" style="padding:70px 18px"><h3>No projects yet.</h3><p>Your first capture will appear here automatically.</p><a class="btn primary" href="/new">Start a capture →</a></div>';
          return;
        }
        panel.innerHTML='<div class="project-data"><div class="project-data-head"><div><h2>Capture history</h2><p>Open a project to inspect its live preview, files, metadata and exports.</p></div><span class="project-count">'+projects.length+' project'+(projects.length===1?"":"s")+'</span></div><div class="project-list"></div></div>';
        const list=$(".project-list",panel);
        projects.forEach(p=>{
          const row=document.createElement("a");
          row.className="project-row";
          row.href="/project/"+p.id;
          row.innerHTML='<div class="project-favicon">'+hostOf(p.source_url).slice(0,1).toUpperCase()+'</div><div class="project-row-main"><b>'+hostOf(p.source_url)+'</b><span>'+p.source_url+'</span></div><span class="status-pill '+p.status+'">'+p.status+'</span><span class="project-meta">'+(p.file_count||0)+' files · '+fmtBytes(p.byte_count)+'</span><span class="row-arrow">→</span>';
          list.appendChild(row);
        });
      }catch(e){
        panel.innerHTML='<div class="project-tab-error">'+String(e.message||"Projects could not be loaded.")+'</div>';
      }
      return;
    }
    const tabs=$$("[data-project-tab]");
    const esc=v=>String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]));
    const previewBase="/preview/"+encodeURIComponent(id)+"/";
    const cache={};
    const loadJson=async file=>{
      if(cache[file]) return cache[file];
      const r=await fetch(previewBase+file,{credentials:"same-origin"});
      if(!r.ok) throw new Error("Project data is not available yet.");
      return cache[file]=await r.json();
    };
    const loadText=async file=>{
      if(cache[file]) return cache[file];
      const r=await fetch(previewBase+file,{credentials:"same-origin"});
      if(!r.ok) throw new Error("Project data is not available yet.");
      return cache[file]=await r.text();
    };
    const prettyBytes=n=>{
      n=Number(n||0);
      if(n<1024)return n+" B";
      if(n<1048576)return (n/1024).toFixed(1)+" KB";
      if(n<1073741824)return (n/1048576).toFixed(1)+" MB";
      return (n/1073741824).toFixed(1)+" GB";
    };
    const extOf=file=>{
      const n=(file||"").split("/").pop()||"";
      const i=n.lastIndexOf(".");
      return i>0?n.slice(i+1).toUpperCase():"FILE";
    };

    try{
      const d=await json("/api/projects/"+id);
      const p=d.project;
      $(".badge.gray") && ($(".badge.gray").textContent=p.status);
      const h=$(".hero-row h1");
      if(h) h.textContent=hostOf(p.source_url);
      const desc=$(".hero-row p");
      if(desc) desc.textContent=p.source_url;

      if(!panel) return;
      if(p.status!=="done"){
        panel.innerHTML='<div class="capture-running"><div class="capture-spinner"></div><h3>'+esc(p.status.charAt(0).toUpperCase()+p.status.slice(1))+' capture</h3><p>WebLoom is building the project. This page updates automatically.</p></div>';
        tabs.forEach(t=>t.classList.add("loading"));
        setTimeout(()=>location.reload(),3500);
        return;
      }

      const hero=$(".hero-row");
      if(hero && !$(".project-actions",hero)){
        hero.insertAdjacentHTML("beforeend",'<div class="project-actions"><a class="btn" target="_blank" rel="noopener" href="'+previewBase+'">Open preview ↗</a><a class="btn primary" href="/api/jobs/'+encodeURIComponent(id)+'/download">Download ZIP ↓</a></div>');
      }

      const render=async tab=>{
        tabs.forEach(a=>a.classList.toggle("active",a.dataset.projectTab===tab));
        panel.innerHTML='<div class="capture-running" style="min-height:360px"><div class="capture-spinner"></div><p>Loading '+esc(tab)+'…</p></div>';
        try{
          if(tab==="overview"){
            panel.innerHTML='<iframe class="project-preview-frame" title="Captured website preview" src="'+previewBase+'" sandbox="allow-scripts allow-forms allow-popups" referrerpolicy="no-referrer"></iframe>';
            return;
          }
          if(tab==="pages"){
            const m=await loadJson("webloom-project.json");
            const pages=Array.isArray(m.pages)?m.pages:[];
            panel.innerHTML='<div class="project-data"><div class="project-data-head"><div><h2>Pages</h2><p>Reachable public routes saved with this capture.</p></div><span class="project-count">'+pages.length+' found</span></div><div class="data-list">'+pages.map((url,i)=>'<div class="data-row"><a target="_blank" rel="noopener" href="'+previewBase+(i===0?"":encodeURI(new URL(url).pathname.replace(/^\//,"")))+'">'+esc(url)+'</a><span>page</span></div>').join("")+'</div></div>';
            return;
          }
          if(tab==="assets"){
            const m=await loadJson("webloom-project.json");
            const files=Array.isArray(m.files)?m.files:[];
            const visible=files.slice(0,180);
            panel.innerHTML='<div class="project-data"><div class="project-data-head"><div><h2>Files & assets</h2><p>Captured frontend files kept with their project paths.</p></div><span class="project-count">'+files.length+' files</span></div><div class="asset-grid">'+visible.map(f=>'<div class="asset-item"><span class="asset-type">'+esc(extOf(f.path))+'</span><b title="'+esc(f.path)+'">'+esc(f.path)+'</b><span>'+prettyBytes(f.bytes)+'</span></div>').join("")+'</div>'+(files.length>visible.length?'<p class="metric-sub">Showing the first '+visible.length+' files.</p>':"")+'</div>';
            return;
          }
          if(tab==="metadata"){
            const m=await loadJson("metadata.json");
            const og=m.open_graph||{};
            panel.innerHTML='<div class="project-data"><div class="project-data-head"><div><h2>Metadata</h2><p>Page metadata separated from the raw markup.</p></div></div><div class="meta-grid"><div class="meta-card"><small>Title</small><div>'+esc(m.title||"Not found")+'</div></div><div class="meta-card"><small>Description</small><div>'+esc(m.description||"Not found")+'</div></div><div class="meta-card"><small>Canonical</small><div>'+esc(m.canonical||"Not found")+'</div></div><div class="meta-card"><small>Open Graph</small><div>'+(Object.keys(og).length?Object.entries(og).map(([k,v])=>'<b>'+esc(k)+'</b>: '+esc(v)).join("<br>"):"Not found")+'</div></div></div></div>';
            return;
          }
          if(tab==="links"){
            const links=await loadJson("links.json");
            const rows=Array.isArray(links)?links:[];
            panel.innerHTML='<div class="project-data"><div class="project-data-head"><div><h2>Discovered links</h2><p>URLs referenced while WebLoom mapped the public frontend.</p></div><span class="project-count">'+rows.length+' links</span></div><div class="data-list">'+rows.slice(0,250).map(url=>'<div class="data-row"><a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(url)+'</a><span>'+esc(hostOf(url))+'</span></div>').join("")+'</div></div>';
            return;
          }
          if(tab==="sitemap"){
            const xml=await loadText("sitemap.xml");
            panel.innerHTML='<div class="project-data"><div class="project-data-head"><div><h2>Sitemap</h2><p>Generated from the public routes WebLoom actually discovered.</p></div><a class="btn" href="'+previewBase+'sitemap.xml" target="_blank" rel="noopener">Open XML ↗</a></div><pre class="code-panel">'+esc(xml)+'</pre></div>';
            return;
          }
          if(tab==="export"){
            const m=await loadJson("webloom-project.json");
            panel.innerHTML='<div class="project-data"><div class="project-data-head"><div><h2>Export</h2><p>Take the organized project with you.</p></div></div><div class="export-box"><div><h3>WebLoom project ZIP</h3><p>'+esc(String(m.files_saved||0))+' captured files · '+prettyBytes(m.bytes_saved||0)+' · metadata, links and sitemap included.</p></div><a class="btn primary" href="/api/jobs/'+encodeURIComponent(id)+'/download">Download ZIP ↓</a></div></div>';
            return;
          }
        }catch(err){
          panel.innerHTML='<div class="project-tab-error">'+esc(err.message||"Could not load this project view.")+'</div>';
        }
      };

      tabs.forEach(a=>a.addEventListener("click",e=>{
        e.preventDefault();
        const tab=a.dataset.projectTab||"overview";
        history.replaceState(null,"","#"+tab);
        render(tab);
      }));
      const initial=(location.hash||"#overview").slice(1);
      render(tabs.some(a=>a.dataset.projectTab===initial)?initial:"overview");
    }catch(e){
      if(panel) panel.innerHTML='<div class="project-tab-error">'+esc(e.message||"Project could not be loaded.")+'</div>';
    }
  }

  async function billingPage(){
    if(path!=="/billing") return;
    const u=await hydrateUser();
    if(!u) return;
    const upgrade=$("#upgradePro");
    const notice=$(".notice");

    if(u.role==="owner"){
      if(upgrade){
        upgrade.disabled=true;
        upgrade.textContent="Owner access active";
      }
      if(notice) notice.textContent="Owner access is active. This account does not need a Pro subscription.";
      return;
    }

    if(u.plan==="pro" && ["active","trialing"].includes(u.subscription_status)){
      if(upgrade){
        upgrade.textContent="Manage billing →";
        upgrade.addEventListener("click",async()=>{
          const old=upgrade.textContent;
          upgrade.disabled=true;upgrade.textContent="Opening billing…";
          try{
            const d=await apiPost("/api/billing/portal");
            location.href=d.url;
          }catch(e){
            upgrade.disabled=false;upgrade.textContent=old;if(notice){notice.textContent=e.message;notice.classList.add("error");}
          }
        });
      }
      if(notice) notice.textContent="WebLoom Pro is active on this account.";
      return;
    }

    if(u.is_anonymous || !u.email){
      if(upgrade){
        upgrade.textContent="Create account for Pro →";
        upgrade.addEventListener("click",()=>{location.href="/signup?next="+encodeURIComponent("/billing")});
      }
      if(notice) notice.textContent="Your free capture does not need an account. Create or sign in to an account only when you want Pro.";
      return;
    }

    if(upgrade){
      upgrade.addEventListener("click",async()=>{
        const old=upgrade.textContent;
        upgrade.disabled=true;upgrade.textContent="Opening checkout…";
        try{
          const d=await apiPost("/api/billing/checkout");
          location.href=d.url;
        }catch(e){
          if(e.data?.account_required){
            location.href="/signup?next="+encodeURIComponent("/billing");
            return;
          }
          upgrade.disabled=false;upgrade.textContent=old;if(notice){notice.textContent=e.message;notice.classList.add("error");}
        }
      });
    }
    if(notice) notice.textContent="Your free capture is ready. Upgrade when you need unlimited captures.";
  }

  function recoveryPage(){
    if(path!=="/forgot") return;
    const form=$("#recoverForm");
    const button=$("#recoverButton");
    const email=$("#recoverEmail");
    const status=$("#recoverStatus");
    if(!form||!button||!email||!status) return;
    form.addEventListener("submit",async e=>{
      e.preventDefault();
      status.textContent="";
      const old=button.textContent;
      button.disabled=true;
      button.textContent="Sending…";
      try{
        await apiPost("/api/auth/recover",{email:email.value.trim()});
        status.classList.add("ok");
        status.textContent="Recovery email sent. Check your inbox.";
        button.textContent="Email sent";
      }catch(err){
        status.classList.remove("ok");
        status.textContent=err.message;
        button.disabled=false;
        button.textContent=old;
      }
    });
  }

  async function accountPage(){
    if(path!=="/account") return;
    const u=await hydrateUser();
    if(!u) return;
    const name=$("#accountName"), email=$("#accountEmail"), save=$("#saveAccount"), status=$("#accountStatus");
    if(name) name.value=u.display_name||"";
    if(email) email.value=u.email||"";
    save?.addEventListener("click",async()=>{
      const old=save.textContent;
      save.disabled=true; save.textContent="Saving…";
      if(status) status.textContent="";
      try{
        const d=await apiPatch("/api/account",{display_name:name?.value.trim()||""});
        if(status){status.className="form-status ok";status.textContent="Account saved.";}
        if(d.profile?.display_name && name) name.value=d.profile.display_name;
      }catch(err){
        if(status){status.className="form-status error";status.textContent=err.message;}
      }finally{
        save.disabled=false;save.textContent=old;
      }
    });
  }

  async function settingsPage(){
    if(path!=="/settings") return;
    const u=await hydrateUser();
    if(!u) return;
    const settings=u.settings||{};
    const capture=$("#captureMode"), format=$("#exportFormat"), naming=$("#projectNaming"), save=$("#saveSettings"), status=$("#settingsStatus");
    if(capture) capture.value=settings.capture_mode||"standard";
    if(format) format.value=settings.export_format||"zip";
    if(naming) naming.value=settings.project_naming||"hostname";
    const deepOption=capture?.querySelector('option[value="deep"]');
    if(deepOption && u.role!=="owner" && u.plan!=="pro") deepOption.disabled=true;
    save?.addEventListener("click",async()=>{
      const old=save.textContent;save.disabled=true;save.textContent="Saving…";
      if(status) status.textContent="";
      try{
        await apiPatch("/api/settings",{
          capture_mode:capture?.value||"standard",
          export_format:format?.value||"zip",
          project_naming:naming?.value||"hostname"
        });
        if(status){status.className="form-status ok";status.textContent="Defaults saved.";}
      }catch(err){
        if(status){status.className="form-status error";status.textContent=err.message;}
      }finally{save.disabled=false;save.textContent=old;}
    });
  }

  async function signoutButtons(){
    $$("[data-signout]").forEach(btn=>btn.addEventListener("click",async()=>{await apiPost("/api/auth/signout");location.href="/";}));
  }

  if(path==="/signin") authPage("signin");
  if(path==="/signup") authPage("signup");
  recoveryPage();
  if(path==="/dashboard") dashboard(); else hydrateUser();
  projectPage();
  billingPage();
  accountPage();
  settingsPage();
  signoutButtons();

  let motionFrame=0;
  addEventListener("scroll",()=>{
    if(motionFrame) return;
    motionFrame=requestAnimationFrame(()=>{
      motionFrame=0;
      const y=Math.max(-36,Math.min(72,scrollY*.035));
      document.documentElement.style.setProperty("--app-orb-y",y+"px");
    });
  },{passive:true});
})();

(function(){
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const surfaces = [...document.querySelectorAll(".motion-surface")];
  surfaces.forEach(el=>{
    if(reduced) return;
    el.addEventListener("pointermove",e=>{
      const r=el.getBoundingClientRect();
      el.style.setProperty("--mx",((e.clientX-r.left)/r.width*100).toFixed(2)+"%");
      el.style.setProperty("--my",((e.clientY-r.top)/r.height*100).toFixed(2)+"%");
      const ry=((e.clientX-r.left)/r.width-.5)*3;
      const rx=((e.clientY-r.top)/r.height-.5)*-3;
      el.style.setProperty("--depth-rx",rx.toFixed(2)+"deg");
      el.style.setProperty("--depth-ry",ry.toFixed(2)+"deg");
    });
    el.addEventListener("pointerleave",()=>{
      el.style.setProperty("--mx","50%");
      el.style.setProperty("--my","50%");
      el.style.setProperty("--depth-rx","0deg");
      el.style.setProperty("--depth-ry","0deg");
    });
  });

  const scrubbers=[...document.querySelectorAll("[data-scrub]")];
  const depthNodes=[...document.querySelectorAll("[data-depth]")];
  if(!scrubbers.length && !depthNodes.length) return;
  let raf=0;
  const update=()=>{
    raf=0;
    const vh=innerHeight||1;
    scrubbers.forEach(el=>{
      const r=el.getBoundingClientRect();
      const raw=(vh-r.top)/(vh+r.height);
      const p=Math.max(0,Math.min(1,raw));
      el.style.setProperty("--scrub",p.toFixed(4));
      el.style.setProperty("--scrub-y",((1-p)*18).toFixed(2)+"px");
      el.style.setProperty("--scrub-scale",(0.985+p*.015).toFixed(4));
    });
    depthNodes.forEach((el,i)=>{
      const r=el.getBoundingClientRect();
      const center=r.top+r.height/2;
      const delta=(center-vh/2)/vh;
      const strength=Number(el.dataset.depth||1);
      el.style.setProperty("--depth-y",(-delta*18*strength).toFixed(2)+"px");
    });
  };
  const requestUpdate=()=>{if(!raf)raf=requestAnimationFrame(update)};
  addEventListener("scroll",requestUpdate,{passive:true});
  addEventListener("resize",requestUpdate);
  update();
})();
