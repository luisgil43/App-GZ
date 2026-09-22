from .storage import download_json, list_all_objects


def logical_sample_id(annotation):
    local_sample_id = annotation.get("local_sample_id")

    if local_sample_id:
        return str(local_sample_id)

    sample_id = annotation.get("sample_id")

    if sample_id:
        return str(sample_id)

    return None


def scan_cloud_samples():
    objects = list_all_objects()

    object_map = {str(item.get("Key")): item for item in objects if item.get("Key")}

    annotation_keys = sorted(
        key
        for key in object_map
        if (
            key.startswith("annotations/")
            and key.endswith(".json")
            and int(
                object_map[key].get(
                    "Size",
                    0,
                )
            )
            > 0
        )
    )

    valid_samples = []
    invalid_samples = []
    groups = {}

    for annotation_key in annotation_keys:
        try:
            annotation = download_json(key=annotation_key)

            if not isinstance(
                annotation,
                dict,
            ):
                raise RuntimeError("La anotación no contiene un objeto JSON.")

            logical_id = logical_sample_id(annotation)

            if not logical_id:
                raise RuntimeError("No tiene local_sample_id ni sample_id.")

            storage = annotation.get("storage") or {}

            image_key = storage.get("image_key")

            if not image_key:
                raise RuntimeError("No tiene storage.image_key.")

            image_object = object_map.get(image_key)

            if not image_object:
                raise RuntimeError(f"No existe imagen {image_key}.")

            if (
                int(
                    image_object.get(
                        "Size",
                        0,
                    )
                )
                <= 0
            ):
                raise RuntimeError("La imagen está vacía.")

            sample = {
                "logical_id": logical_id,
                "sample_id": annotation.get("sample_id"),
                "local_sample_id": annotation.get("local_sample_id"),
                "annotation_key": annotation_key,
                "image_key": image_key,
                "annotation": annotation,
                "annotation_size": int(
                    object_map[annotation_key].get(
                        "Size",
                        0,
                    )
                ),
                "image_size": int(
                    image_object.get(
                        "Size",
                        0,
                    )
                ),
            }

            valid_samples.append(sample)

            groups.setdefault(
                logical_id,
                [],
            ).append(sample)

        except Exception as exc:
            invalid_samples.append(
                {
                    "annotation_key": (annotation_key),
                    "error": str(exc),
                }
            )

    unique_samples = {}
    duplicate_samples = []

    for logical_id, samples in groups.items():
        ordered = sorted(
            samples,
            key=lambda item: item["annotation_key"],
        )

        canonical = ordered[0]

        unique_samples[logical_id] = canonical

        duplicate_samples.extend(ordered[1:])

    return {
        "objects_total": len(objects),
        "annotation_total": len(annotation_keys),
        "valid_pair_total": len(valid_samples),
        "unique_total": len(unique_samples),
        "duplicate_total": len(duplicate_samples),
        "invalid_total": len(invalid_samples),
        "unique_samples": unique_samples,
        "duplicate_samples": (duplicate_samples),
        "invalid_samples": (invalid_samples),
    }
