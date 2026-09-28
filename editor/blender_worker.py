"""Executado somente pelo Blender; recebe JSON validado, nunca código do modelo."""
import json
import math
from pathlib import Path
import sys


def main():
    import bpy
    from mathutils import Vector
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    import blender_modelos as MOD
    spec=Path(sys.argv[sys.argv.index("--")+1])
    d=json.loads(spec.read_text(encoding="utf-8"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene
    scene.render.engine="CYCLES"
    scene.cycles.device="CPU"
    scene.cycles.samples=d.get("amostras",16)
    scene.cycles.use_denoising=True
    scene.render.use_persistent_data=True
    # ilustração limpa não precisa de luz rebatendo 12 vezes: metade do tempo
    c=scene.cycles
    c.max_bounces=4;c.diffuse_bounces=2;c.glossy_bounces=2;c.transmission_bounces=2
    c.caustics_reflective=False;c.caustics_refractive=False
    c.adaptive_threshold=0.05
    # cor da marca exata: sem a curva "filmica", o coral continua coral
    try: scene.view_settings.view_transform="Standard"
    except TypeError: pass
    scene.render.resolution_x=d["largura"];scene.render.resolution_y=d["altura"]
    scene.render.resolution_percentage=100
    scene.render.fps=d["fps"]
    scene.render.film_transparent=True
    scene.render.image_settings.file_format="PNG"
    scene.render.image_settings.color_mode="RGBA"
    scene.render.filepath=str(spec.parent/"quadro_")
    scene.frame_start=1;scene.frame_end=math.ceil(d["duracao"]*d["fps"])
    scene.world=bpy.data.worlds.new("Ambiente")
    scene.world.use_nodes=True
    fundo=(0.12,0.12,0.12,1) if not d.get("estudio") else (0.9,0.88,0.86,1)
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value=fundo
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value=0.5 if not d.get("estudio") else 0.55
    camdata=bpy.data.cameras.new("Camera");cam=bpy.data.objects.new("Camera",camdata)
    scene.collection.objects.link(cam);scene.camera=cam
    cam.location=d["camera"]["posicao"]
    cam.rotation_euler=(Vector(d["camera"]["alvo"])-cam.location).to_track_quat("-Z","Y").to_euler()
    camdata.lens=d["camera"]["lente"]
    if d["camera"].get("orto"):
        camdata.type="ORTHO";camdata.ortho_scale=d["camera"]["orto"]
    alvo=bpy.data.objects.new("Alvo da câmera",None);scene.collection.objects.link(alvo)
    alvo.location=d["camera"]["alvo"]
    track=cam.constraints.new(type="TRACK_TO");track.target=alvo;track.track_axis="TRACK_NEGATIVE_Z";track.up_axis="UP_Y"
    cam.keyframe_insert(data_path="location",frame=1);alvo.keyframe_insert(data_path="location",frame=1)
    camdata.keyframe_insert(data_path="lens",frame=1)
    for marco in sorted(d["camera"].get("marcos",[]),key=lambda m:m["t"]):
        frame=1+round(marco["t"]*d["fps"])
        if "posicao" in marco: cam.location=marco["posicao"];cam.keyframe_insert(data_path="location",frame=frame)
        if "alvo" in marco: alvo.location=marco["alvo"];alvo.keyframe_insert(data_path="location",frame=frame)
        if "lente" in marco: camdata.lens=marco["lente"];camdata.keyframe_insert(data_path="lens",frame=frame)
    luzes=[("Principal",(3,-4,7),1000,5),("Recorte",(-4,2,5),1400,4),("Preenchimento",(0,-3,1),180,3)]
    if d.get("estudio"):
        # luz de estúdio de ilustração 3D: chave grande e macia, recorte frio, sem sombra dura
        luzes=[("Principal",(4,-5,7),650,7),("Recorte",(-5,4,5),520,5),("Preenchimento",(-3,-6,2),160,6)]
    for nome,pos,energia,tam in luzes:
        luz=bpy.data.lights.new(nome,"AREA");luz.energy=energia;luz.shape="DISK";luz.size=tam
        obj=bpy.data.objects.new(nome,luz);scene.collection.objects.link(obj);obj.location=pos
        obj.rotation_euler=(-obj.location).to_track_quat("-Z","Y").to_euler()
    if d.get("sombra"):
        # o chão invisível que só recebe a sombra: o objeto assenta no vídeo
        bpy.ops.mesh.primitive_plane_add(size=40)
        chao=bpy.context.object;chao.name="Chao_sombra";chao.is_shadow_catcher=True
    if d.get("transicao"):
        scene.render.use_motion_blur=True
        MOD.transicao_faixas(d["transicao"].get("paleta"),d["transicao"]["largura"],
                             d["transicao"]["altura"],d["fps"],d["duracao"])
    for n,raw in enumerate(d["objetos"]):
        tipo=raw["tipo"]
        if tipo=="modelo":
            MOD.construir(raw,d["fps"],d["duracao"]);continue
        if tipo=="cubo": bpy.ops.mesh.primitive_cube_add()
        elif tipo=="esfera": bpy.ops.mesh.primitive_uv_sphere_add(segments=48,ring_count=24)
        elif tipo=="torus": bpy.ops.mesh.primitive_torus_add(major_segments=64,minor_segments=24)
        elif tipo=="cilindro": bpy.ops.mesh.primitive_cylinder_add(vertices=64)
        elif tipo=="plano": bpy.ops.mesh.primitive_plane_add(size=2)
        else:
            bpy.ops.object.text_add()
            bpy.context.object.data.body=raw["texto"]
            bpy.context.object.data.align_x="CENTER";bpy.context.object.data.align_y="CENTER"
            bpy.context.object.data.extrude=0.055;bpy.context.object.data.bevel_depth=0.009
        obj=bpy.context.object;obj.name=f"Arte_{n:02d}"
        if tipo in ("cubo","cilindro"):
            mod=obj.modifiers.new("Bordas suaves","BEVEL");mod.width=0.08;mod.segments=3
        if obj.type=="MESH":
            for poly in obj.data.polygons: poly.use_smooth=tipo in ("esfera","torus","cilindro")
        mat=bpy.data.materials.new(f"Material_{n:02d}");mat.use_nodes=True
        bsdf=mat.node_tree.nodes.get("Principled BSDF")
        srgb=[int(raw["cor"][i:i+2],16)/255 for i in (1,3,5)]
        linear=[v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4 for v in srgb]
        bsdf.inputs["Base Color"].default_value=(*linear,1)
        bsdf.inputs["Metallic"].default_value=raw["metalico"]
        bsdf.inputs["Roughness"].default_value=raw["rugosidade"]
        obj.data.materials.append(mat)
        mapa={"posicao":"location","rotacao":"rotation_euler","escala":"scale"}
        for k,path in mapa.items():
            valor=raw[k]
            if k=="rotacao": valor=[math.radians(x) for x in valor]
            setattr(obj,path,valor)
            obj.keyframe_insert(data_path=path,frame=1)
        for marco in sorted(raw["marcos"],key=lambda m:m["t"]):
            for k,path in mapa.items():
                if k in marco:
                    valor=marco[k]
                    if k=="rotacao": valor=[math.radians(x) for x in valor]
                    setattr(obj,path,valor)
                    obj.keyframe_insert(data_path=path,frame=1+round(marco["t"]*d["fps"]))
    if d["camera"].get("enquadrar"):
        MOD.enquadrar(cam,alvo,[o for o in scene.objects if o.name!="Chao_sombra"],scene)
    bpy.ops.wm.save_as_mainfile(filepath=str(spec.parent/"cena.blend"))
    bpy.ops.render.render(animation=True)


if __name__=="__main__": main()
