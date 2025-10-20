package tema2.t02;

import java.util.Scanner;

public class ej03 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Cual es la nota (del 1 al 10)");
        int nota = sc.nextInt();

        if (nota >= 5 && nota <= 10){
            System.out.println(nota + " has aprobado");
        } if (nota < 5 && nota >= 0){
            System.out.println(nota + " es insuficiente para aprobar");
        } else {
            System.out.println("has introducido un formato incorrecto");
        }
    }
}
